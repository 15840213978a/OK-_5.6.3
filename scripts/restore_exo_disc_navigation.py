#!/usr/bin/env python3
"""Restore the EXO optical-disc navigation stream around FongMi's verified 5.6.7 JNI.

This step runs after restore_official_java_bridges.py. It upgrades the recovered
IsoNavigationSession to the 5.6.7 JNI surface, adds a navigation-backed DataSource,
routes IsoMediaSource through nativeRead() for DVD/Blu-ray, and exposes the active
navigation source to ExoPlayer.
"""

from pathlib import Path
import argparse


DISC_SOURCE = r'''/*
 * Copyright (C) 2026 The Android Open Source Project
 * Licensed under the Apache License, Version 2.0.
 */
package androidx.media3.exoplayer.source;

import androidx.media3.common.util.UnstableApi;

/** Source-level optical-disc navigation controls used by ExoPlayer. */
@UnstableApi
public interface DiscNavigationSource {
  boolean isDiscNavigationPlayback();
  boolean hasDiscMenu();
  boolean isDiscMenuActive();
  boolean sendDiscMenuAction(String action);
  boolean sendDiscMenuPointer(float x, float y, boolean activate);
  boolean seekDiscTimeUs(long timeUs);
  boolean seekDiscChapter(int chapterIndex);
}
'''

NAV_DATA_SOURCE = r'''/*
 * Copyright (C) 2026 The Android Open Source Project
 * Licensed under the Apache License, Version 2.0.
 */
package androidx.media3.exoplayer.source.iso;

import android.net.Uri;
import androidx.annotation.Nullable;
import androidx.media3.common.C;
import androidx.media3.datasource.BaseDataSource;
import androidx.media3.datasource.DataSource;
import androidx.media3.datasource.DataSpec;
import androidx.media3.exoplayer.iso.IsoNavigationSession;
import java.io.ByteArrayOutputStream;
import java.io.IOException;

/**
 * Progressive DataSource backed by {@link IsoNavigationSession#read}.
 *
 * <p>The active native navigation stream is not byte-seekable. Bytes already emitted for the
 * current navigation segment are retained so extractor reopen/retry requests can replay the
 * completed prefix without rewinding the native state machine.
 */
final class IsoNavigationDataSource extends BaseDataSource {

  static final class Factory implements DataSource.Factory {
    private final IsoNavigationSession session;
    private final Uri isoUri;
    private final State state = new State();

    Factory(IsoNavigationSession session, Uri isoUri) {
      this.session = session;
      this.isoUri = isoUri.buildUpon().fragment(null).build();
    }

    @Override
    public DataSource createDataSource() {
      return new IsoNavigationDataSource(session, isoUri, state);
    }

    void resetSegment() {
      synchronized (state) {
        state.generation++;
        state.replay.reset();
        state.owner = null;
      }
    }
  }

  private static final class State {
    final ByteArrayOutputStream replay = new ByteArrayOutputStream(256 * 1024);
    int generation;
    @Nullable IsoNavigationDataSource owner;
  }

  private final IsoNavigationSession session;
  private final Uri isoUri;
  private final State state;

  @Nullable private Uri openedUri;
  private long readPosition;
  private int generation;
  private boolean opened;
  private boolean owner;

  private IsoNavigationDataSource(IsoNavigationSession session, Uri isoUri, State state) {
    super(/* isNetwork= */ false);
    this.session = session;
    this.isoUri = isoUri;
    this.state = state;
  }

  @Override
  public long open(DataSpec dataSpec) throws IOException {
    transferInitializing(dataSpec);
    Uri requested = dataSpec.uri.buildUpon().fragment(null).build();
    if (!isoUri.equals(requested)) {
      throw new IOException("Disc navigation URI changed: " + requested);
    }
    synchronized (state) {
      generation = state.generation;
      if (dataSpec.position > state.replay.size()) {
        throw new IOException("Disc navigation stream is not byte seekable beyond replayed bytes");
      }
      readPosition = dataSpec.position;
      if (state.owner == null) {
        state.owner = this;
        owner = true;
      }
    }
    openedUri = requested;
    opened = true;
    transferStarted(dataSpec);
    return dataSpec.length == C.LENGTH_UNSET ? C.LENGTH_UNSET : dataSpec.length;
  }

  @Override
  public int read(byte[] buffer, int offset, int length) throws IOException {
    if (!opened) {
      throw new IOException("Disc navigation source is not open");
    }
    if (length == 0) {
      return 0;
    }

    synchronized (state) {
      if (generation != state.generation) {
        generation = state.generation;
        readPosition = 0;
        owner = state.owner == null || state.owner == this;
        if (owner) {
          state.owner = this;
        }
      }
      byte[] replay = state.replay.toByteArray();
      if (readPosition < replay.length) {
        int count = (int) Math.min(length, replay.length - readPosition);
        System.arraycopy(replay, (int) readPosition, buffer, offset, count);
        readPosition += count;
        bytesTransferred(count);
        return count;
      }
      if (!owner) {
        return C.RESULT_END_OF_INPUT;
      }
    }

    int result = session.read(buffer, offset, length, /* flags= */ 0);
    if (result <= 0) {
      return result == 0 ? 0 : C.RESULT_END_OF_INPUT;
    }
    synchronized (state) {
      state.replay.write(buffer, offset, result);
      readPosition += result;
    }
    bytesTransferred(result);
    return result;
  }

  @Nullable
  @Override
  public Uri getUri() {
    return openedUri;
  }

  @Override
  public void close() {
    if (!opened) {
      return;
    }
    synchronized (state) {
      if (state.owner == this) {
        state.owner = null;
      }
    }
    opened = false;
    openedUri = null;
    owner = false;
    transferEnded();
  }
}
'''


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"anchor not found for {label}")
    return text.replace(old, new, 1)


def upgrade_session(root: Path) -> None:
    path = root / "libraries/exoplayer/src/main/java/androidx/media3/exoplayer/iso/IsoNavigationSession.java"
    text = path.read_text(encoding="utf-8")

    text = replace_once(
        text,
        "  public static final int DISC_TYPE_BLURAY = 2;\n",
        """  public static final int DISC_TYPE_BLURAY = 2;
  public static final int ACTION_UP = 1;
  public static final int ACTION_DOWN = 2;
  public static final int ACTION_LEFT = 3;
  public static final int ACTION_RIGHT = 4;
  public static final int ACTION_SELECT = 5;
  public static final int ACTION_MENU = 6;
  public static final int ACTION_POPUP = 7;
  public static final int ACTION_PREV = 8;
  public static final int ACTION_TITLE_MENU = 11;
""",
        "action constants",
    )

    text = replace_once(
        text,
        "  private volatile int discontinuityId;\n",
        """  private volatile int discontinuityId;
  // Fields present in the verified 5.6.7 JNI contract.
  private long finiteStillDurationMs;
  private long finiteStillId;
  private volatile boolean infiniteStill;
""",
        "5.6.7 native state fields",
    )

    anchor = "  public boolean sendAction(int action) {\n"
    methods = r'''  /** Maps the official 5.6.7 disc-navigation command strings to native actions. */
  public static int actionCode(String action) {
    if (action == null) {
      return 0;
    }
    switch (action) {
      case "up":
        return ACTION_UP;
      case "down":
        return ACTION_DOWN;
      case "left":
        return ACTION_LEFT;
      case "right":
        return ACTION_RIGHT;
      case "select":
        return ACTION_SELECT;
      case "menu":
        return ACTION_MENU;
      case "popup":
        return ACTION_POPUP;
      case "prev":
        return ACTION_PREV;
      case "title-menu":
        return ACTION_TITLE_MENU;
      default:
        return 0;
    }
  }

  public boolean sendAction(String action) {
    int code = actionCode(action);
    return code != 0 && sendAction(code);
  }

  /**
   * Queues a normalized menu pointer command using the packed format from the official 5.6.7
   * player: action 9 for move and 10 for activation, with 16-bit normalized coordinates.
   */
  public boolean sendPointer(float x, float y, boolean activate) {
    if (!Float.isFinite(x)
        || !Float.isFinite(y)
        || x < 0f
        || x > 1f
        || y < 0f
        || y > 1f
        || !hasMenu) {
      return false;
    }
    long px = Math.round(Math.min(x, Math.nextDown(1.0f)) * 65535.0f);
    long py = Math.round(Math.min(y, Math.nextDown(1.0f)) * 65535.0f);
    long packed = (py << 24) | (px << 8) | (activate ? 10L : 9L);
    synchronized (this) {
      if (nativeHandle == 0) {
        return false;
      }
      pendingAction.set(packed);
      changeSequence++;
      notifyAll();
    }
    schedulePump();
    return true;
  }

  public synchronized boolean isOpen() {
    return nativeHandle != 0;
  }

'''
    text = replace_once(text, anchor, methods + anchor, "navigation action methods")

    text = text.replace(
        "  private static native void nativePump(long handle);",
        "  private static native int nativePump(long handle);",
    )
    if "nativeResumeBlurayPlaylist" not in text:
        text = replace_once(
            text,
            "  private static native boolean nativeResumeDvdWait(long handle, int discontinuity);\n",
            """  private static native boolean nativeResumeDvdWait(long handle, int discontinuity);

  private static native boolean nativeResumeBlurayPlaylist(long handle, int discontinuity);

  private static native boolean nativeSkipFiniteStill(
      long handle, int discontinuity, long stillId);
""",
            "5.6.7 bluray resume JNI",
        )
    path.write_text(text, encoding="utf-8")


def write_media3_sources(root: Path) -> None:
    disc = root / "libraries/exoplayer/src/main/java/androidx/media3/exoplayer/source/DiscNavigationSource.java"
    disc.write_text(DISC_SOURCE, encoding="utf-8")
    nav = root / "libraries/exoplayer/src/main/java/androidx/media3/exoplayer/source/iso/IsoNavigationDataSource.java"
    nav.write_text(NAV_DATA_SOURCE, encoding="utf-8")


def patch_iso_parsed(root: Path) -> None:
    path = root / "libraries/exoplayer/src/main/java/androidx/media3/exoplayer/source/iso/IsoParsedMedia.java"
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "import androidx.media3.datasource.IsoDataReader;\n",
        """import androidx.media3.datasource.IsoDataReader;
import androidx.media3.exoplayer.iso.IsoNavigationSession;
import androidx.media3.exoplayer.source.ProgressiveMediaSource;
import androidx.media3.extractor.DefaultExtractorsFactory;
""",
        "IsoParsedMedia imports",
    )
    text = replace_once(
        text,
        "  @Nullable private final SacdStructure sacd;\n",
        """  @Nullable private final SacdStructure sacd;
  @Nullable private IsoNavigationSession navigationSession;
  @Nullable private IsoNavigationDataSource.Factory navigationDataSourceFactory;
""",
        "IsoParsedMedia navigation fields",
    )

    build_old = """  public MediaSource buildSource(int editionIndex, LoadErrorHandlingPolicy loadErrorHandlingPolicy)
      throws IOException {
    int resolvedEditionIndex = resolveEditionIndex(editionIndex);
    if (sacd != null) {
"""
    build_new = """  public MediaSource buildSource(int editionIndex, LoadErrorHandlingPolicy loadErrorHandlingPolicy)
      throws IOException {
    int resolvedEditionIndex = resolveEditionIndex(editionIndex);
    if (sacd == null) {
      @Nullable IsoNavigationSession session = getOrOpenNavigationSession();
      if (session != null) {
        MediaItem navigationItem =
            mediaItem
                .buildUpon()
                .setUri(isoUri.buildUpon().fragment(null).build())
                .setMimeType(null)
                .build();
        return new ProgressiveMediaSource.Factory(
                checkNotNull(navigationDataSourceFactory), new DefaultExtractorsFactory())
            .setLoadErrorHandlingPolicy(loadErrorHandlingPolicy)
            .createMediaSource(navigationItem);
      }
    }
    if (sacd != null) {
"""
    text = replace_once(text, build_old, build_new, "navigation source selection")

    close_old = """  @Override
  public void close() {
    isoReader.close();
  }
"""
    close_new = """  @Nullable
  synchronized IsoNavigationSession getNavigationSession() {
    return navigationSession;
  }

  synchronized boolean isNavigationPlayback() {
    return navigationSession != null && navigationSession.isOpen();
  }

  synchronized void resetNavigationSegment() {
    if (navigationDataSourceFactory != null) {
      navigationDataSourceFactory.resetSegment();
    }
  }

  @Nullable
  private synchronized IsoNavigationSession getOrOpenNavigationSession() throws IOException {
    if (sacd != null || !IsoNavigationSession.isAvailable()) {
      return null;
    }
    if (navigationSession == null) {
      try {
        navigationSession = IsoNavigationSession.open(isoReader);
      } catch (IOException error) {
        // Preserve the existing static ISO parser as a safe fallback for unsupported discs.
        return null;
      }
      if (navigationSession != null) {
        navigationDataSourceFactory = new IsoNavigationDataSource.Factory(navigationSession, isoUri);
      }
    }
    return navigationSession;
  }

  @Override
  public synchronized void close() {
    if (navigationSession != null) {
      navigationSession.close();
      navigationSession = null;
      navigationDataSourceFactory = null;
    }
    isoReader.close();
  }
"""
    text = replace_once(text, close_old, close_new, "IsoParsedMedia close/navigation access")
    path.write_text(text, encoding="utf-8")


def patch_iso_media_source(root: Path) -> None:
    path = root / "libraries/exoplayer/src/main/java/androidx/media3/exoplayer/source/iso/IsoMediaSource.java"
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "import androidx.media3.exoplayer.drm.DrmSessionManagerProvider;\n",
        """import androidx.media3.exoplayer.drm.DrmSessionManagerProvider;
import androidx.media3.exoplayer.iso.IsoNavigationSession;
""",
        "IsoMediaSource navigation import",
    )
    text = replace_once(
        text,
        "import androidx.media3.exoplayer.source.MediaChapterProvider;\n",
        """import androidx.media3.exoplayer.source.DiscNavigationSource;
import androidx.media3.exoplayer.source.MediaChapterProvider;
""",
        "IsoMediaSource DiscNavigationSource import",
    )
    text = replace_once(
        text,
        "    implements MediaChapterProvider, MediaEditionSelector {",
        "    implements MediaChapterProvider, MediaEditionSelector, DiscNavigationSource {",
        "IsoMediaSource interfaces",
    )

    anchor = """  @Override
  public boolean selectEdition(MediaEdition edition) {
"""
    nav_methods = r'''  @Override
  public boolean isDiscNavigationPlayback() {
    IsoParsedMedia parsed = parsedMedia;
    return parsed != null && parsed.isNavigationPlayback();
  }

  @Override
  public boolean hasDiscMenu() {
    IsoNavigationSession session = navigationSession();
    return session != null && session.hasMenu();
  }

  @Override
  public boolean isDiscMenuActive() {
    IsoNavigationSession session = navigationSession();
    return session != null && session.isMenuActive();
  }

  @Override
  public boolean sendDiscMenuAction(String action) {
    IsoParsedMedia parsed = parsedMedia;
    IsoNavigationSession session = parsed != null ? parsed.getNavigationSession() : null;
    if (session == null || !session.sendAction(action)) {
      return false;
    }
    int code = IsoNavigationSession.actionCode(action);
    if (code == IsoNavigationSession.ACTION_SELECT
        || code == IsoNavigationSession.ACTION_MENU
        || code == IsoNavigationSession.ACTION_POPUP
        || code == IsoNavigationSession.ACTION_PREV
        || code == IsoNavigationSession.ACTION_TITLE_MENU) {
      parsed.resetNavigationSegment();
    }
    return true;
  }

  @Override
  public boolean sendDiscMenuPointer(float x, float y, boolean activate) {
    IsoParsedMedia parsed = parsedMedia;
    IsoNavigationSession session = parsed != null ? parsed.getNavigationSession() : null;
    if (session == null || !session.sendPointer(x, y, activate)) {
      return false;
    }
    if (activate) {
      parsed.resetNavigationSegment();
    }
    return true;
  }

  @Override
  public boolean seekDiscTimeUs(long timeUs) {
    IsoParsedMedia parsed = parsedMedia;
    IsoNavigationSession session = parsed != null ? parsed.getNavigationSession() : null;
    if (session == null) {
      return false;
    }
    try {
      if (!session.seekToTimeUs(timeUs)) {
        return false;
      }
      parsed.resetNavigationSegment();
      return true;
    } catch (IOException error) {
      sourceError = error;
      return false;
    }
  }

  @Override
  public boolean seekDiscChapter(int chapterIndex) {
    IsoParsedMedia parsed = parsedMedia;
    IsoNavigationSession session = parsed != null ? parsed.getNavigationSession() : null;
    if (session == null) {
      return false;
    }
    try {
      if (!session.seekToChapter(chapterIndex)) {
        return false;
      }
      parsed.resetNavigationSegment();
      return true;
    } catch (IOException error) {
      sourceError = error;
      return false;
    }
  }

  @Nullable
  private IsoNavigationSession navigationSession() {
    IsoParsedMedia parsed = parsedMedia;
    return parsed != null ? parsed.getNavigationSession() : null;
  }

'''
    text = replace_once(text, anchor, nav_methods + anchor, "IsoMediaSource navigation API")

    old_select = """  private boolean selectEdition(int editionIndex, IsoParsedMedia parsed) {
    try {
      prepareEditionSource(editionIndex, parsed);
    } catch (IOException e) {
      sourceError = e;
      return false;
    }
"""
    new_select = """  private boolean selectEdition(int editionIndex, IsoParsedMedia parsed) {
    IsoNavigationSession session = parsed.getNavigationSession();
    if (session != null) {
      try {
        if (!session.selectTitle(editionIndex)) {
          return false;
        }
        parsed.resetNavigationSegment();
      } catch (IOException e) {
        sourceError = e;
        return false;
      }
    }
    try {
      prepareEditionSource(editionIndex, parsed);
    } catch (IOException e) {
      sourceError = e;
      return false;
    }
"""
    text = replace_once(text, old_select, new_select, "navigation edition selection")
    path.write_text(text, encoding="utf-8")


def patch_exoplayer(root: Path) -> None:
    exo = root / "libraries/exoplayer/src/main/java/androidx/media3/exoplayer/ExoPlayer.java"
    text = exo.read_text(encoding="utf-8")
    marker = "public interface ExoPlayer extends Player {\n"
    additions = r'''public interface ExoPlayer extends Player {

  /** Whether the current source is driven by the optical-disc navigation state machine. */
  @UnstableApi
  default boolean isDiscNavigationPlayback() {
    return false;
  }

  /** Whether the current optical disc exposes an interactive menu. */
  @UnstableApi
  default boolean hasDiscMenu() {
    return false;
  }

  @UnstableApi
  default boolean isDiscMenuActive() {
    return false;
  }

  @UnstableApi
  default boolean sendDiscMenuAction(String action) {
    return false;
  }

  @UnstableApi
  default boolean sendDiscMenuPointer(float x, float y, boolean activate) {
    return false;
  }
'''
    text = replace_once(text, marker, additions, "ExoPlayer disc API")
    exo.write_text(text, encoding="utf-8")

    impl = root / "libraries/exoplayer/src/main/java/androidx/media3/exoplayer/ExoPlayerImpl.java"
    text = impl.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "import androidx.media3.exoplayer.source.MaskingMediaSource;\n",
        """import androidx.media3.exoplayer.source.DiscNavigationSource;
import androidx.media3.exoplayer.source.MaskingMediaSource;
""",
        "ExoPlayerImpl disc import",
    )

    anchor = """  @Override
  public List<MediaChapter> getCurrentMediaChapters() {
"""
    methods = r'''  @Override
  public boolean isDiscNavigationPlayback() {
    verifyApplicationThread();
    DiscNavigationSource source = getCurrentSourceAs(playbackInfo, DiscNavigationSource.class);
    return source != null && source.isDiscNavigationPlayback();
  }

  @Override
  public boolean hasDiscMenu() {
    verifyApplicationThread();
    DiscNavigationSource source = getCurrentSourceAs(playbackInfo, DiscNavigationSource.class);
    return source != null && source.hasDiscMenu();
  }

  @Override
  public boolean isDiscMenuActive() {
    verifyApplicationThread();
    DiscNavigationSource source = getCurrentSourceAs(playbackInfo, DiscNavigationSource.class);
    return source != null && source.isDiscMenuActive();
  }

  @Override
  public boolean sendDiscMenuAction(String action) {
    verifyApplicationThread();
    DiscNavigationSource source = getCurrentSourceAs(playbackInfo, DiscNavigationSource.class);
    boolean accepted = source != null && source.sendDiscMenuAction(action);
    if (accepted
        && ("select".equals(action)
            || "menu".equals(action)
            || "popup".equals(action)
            || "prev".equals(action)
            || "title-menu".equals(action))) {
      seekToDefaultPosition();
    }
    return accepted;
  }

  @Override
  public boolean sendDiscMenuPointer(float x, float y, boolean activate) {
    verifyApplicationThread();
    DiscNavigationSource source = getCurrentSourceAs(playbackInfo, DiscNavigationSource.class);
    boolean accepted = source != null && source.sendDiscMenuPointer(x, y, activate);
    if (accepted && activate) {
      seekToDefaultPosition();
    }
    return accepted;
  }

'''
    text = replace_once(text, anchor, methods + anchor, "ExoPlayerImpl disc API")

    old_chapter = """  @Override
  public boolean selectChapter(MediaChapter chapter) {
    verifyApplicationThread();
    @Nullable MediaChapter currentChapter = findMediaChapter(currentMediaChapters, chapter.index);
    if (currentChapter == null || currentChapter.timeUs == C.TIME_UNSET) {
      return false;
    }
    seekTo(Util.usToMs(currentChapter.timeUs));
    return true;
  }
"""
    new_chapter = """  @Override
  public boolean selectChapter(MediaChapter chapter) {
    verifyApplicationThread();
    @Nullable MediaChapter currentChapter = findMediaChapter(currentMediaChapters, chapter.index);
    if (currentChapter == null || currentChapter.timeUs == C.TIME_UNSET) {
      return false;
    }
    DiscNavigationSource navigation =
        getCurrentSourceAs(playbackInfo, DiscNavigationSource.class);
    if (navigation != null && navigation.seekDiscChapter(currentChapter.index)) {
      seekToDefaultPosition();
      return true;
    }
    seekTo(Util.usToMs(currentChapter.timeUs));
    return true;
  }
"""
    text = replace_once(text, old_chapter, new_chapter, "navigation chapter selection")
    impl.write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--media3-root", type=Path, required=True)
    args = parser.parse_args()
    root = args.media3_root.resolve()

    upgrade_session(root)
    write_media3_sources(root)
    patch_iso_parsed(root)
    patch_iso_media_source(root)
    patch_exoplayer(root)
    print("Restored EXO optical-disc navigation stream and player controls.")


if __name__ == "__main__":
    main()
