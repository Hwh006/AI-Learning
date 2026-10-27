# 浏览器音轨获取

遵守当前宿主提供的浏览器工具说明。以下是本次已跑通的 Codex CUA 路径；WorkBuddy 使用其实际可用的浏览器资源读取接口，接口不存在时说明限制，不编造调用。

首次 CUA 调用仅进行入口初始化，使用 `begin` 返回的规范视频 URL：

```javascript
let dyTab = await cua.createBrowserTab("chrome", browserUrl, {sessionName: "🎙️ 抖音转写"});
```

读取返回文档和真实 browser ID。若文档公开 `agent.browsers` 及 `pageAssets`，用真实 ID 获取句柄；不要猜测 ID。先读取一次能力文档：

```javascript
let dyBrowser = await agent.browsers.get(observedBrowserId);
let dyMediaTab = await dyBrowser.tabs.get(dyTab.id);
let dyAssets = await dyMediaTab.capabilities.get("pageAssets");
nodeRepl.write(await dyAssets.documentation());
// 下一次调用，按已读文档使用：
let dyInventory = await dyAssets.list();
nodeRepl.write({title: await dyMediaTab.title(),
  audio: dyInventory.assets.filter(a => /media-audio-/.test(a.url))});
```

音轨可能被归类为 `other`，不要只筛选 `kind === 'video'`。已知 `bundle()` 不支持 `other` 音轨，不重复调用失败的 bundle；将真实媒体 URL 交给脚本下载，脚本附带 Referer 和 User-Agent。

页面仍在加载时读取一次新状态再获取资源。若列出多个视频的音轨，核对当前播放对象，只用可确认归属目标视频的地址，不任意取第一项。blob URL 不是可直接下载的 HTTP 地址。

不读取 DOM 隐藏状态、Cookie、浏览器配置文件或未公开内部接口。宿主有专用媒体导出功能时优先按其文档导出，再用 `--audio-file`。所有 UI 操作留在宿主允许的浏览器工具内。

成功后按宿主规则清理临时标签；只有需要用户继续操作时才保留交接页面。
