# OK影视 5.6.8 重构分支

开发分支：`ok-video-568-rebuild`

## 隔离原则

- 独立仓库：`15840213978a/OK-_5.6.3`
- 独立 applicationId：`com.kanglian.okvideo`
- FongMi / 蜂蜜TV 仓库与主分支不再承载 OK 界面开发
- 共享的是播放与解析思路，不再让 OK UI 直接侵入蜂蜜TV主线

## 当前已迁移

- 5.6.8 版本基线标识
- OK影视独立包名
- 首页快捷入口：搜索、推送、本地、收藏、历史、换源
- 本地文件直接进入现有 VideoActivity 播放链
- 手机底部导航显示文字标签

## 后续开发方向

- 参考 OK影视 5.1.6 APK 继续重建首页、搜索、详情、源选择与设置体验
- 保留现有 EXO / MPV / 多源解析能力
- 独立 Actions 构建与 Release
