# 品牌标识

已选方案：**01 阶梯 K 线**（2026-11-22）。

## 使用中的文件

| 用途 | 路径 |
|---|---|
| 侧栏 / 通用图标（SVG） | `web/assets/brand/logo.svg` |
| 浏览器 favicon（16–256 多尺寸 ICO） | `web/favicon.ico` |
| iOS / 触屏图标 180px | `web/assets/brand/apple-touch-icon.png` |
| 256px PNG（备用） | `web/assets/brand/logo-256.png` |

5 个页面均已加入：

```html
<link rel="icon" href="favicon.ico" sizes="any" />
<link rel="icon" type="image/svg+xml" href="assets/brand/logo.svg" />
<link rel="apple-touch-icon" href="assets/brand/apple-touch-icon.png" />
```

侧栏使用 26px 图标；旧 URL `/favicon.ico` 由 `SiteHandler` 302 到 `/web/favicon.ico`。

## 设计源文件

- 全部方案：`docs/brand/option-1…5-*.svg`（含 PNG/ICO/180px）
- 对比页：`docs/brand/preview.html`
- 总览图：`docs/previews/logo-options-20261122.png`
- 16px 实标：`docs/previews/logo-favicon-16px.png`

## 色值

- 主渐变：`#ff6a3d → #e82e5c`
- 图标白：`#ffffff`
- 侧栏底：继承页面 `--bg-sidebar`

## 替换/回滚

改 `web/assets/brand/logo.svg` 与 `web/favicon.ico`（以及 180px）即可；页面链接无需动。
