// FocusGuard 浏览器扩展后台脚本 (Manifest V3)
// 职责：
// - 监听标签页切换与加载事件
// - 精准提取当前活动标签页的 title 与 url
// - 将特征封装为 JSON，并在控制台输出（后续将接入本地 Python 服务与 LLM）

/**
 * 判断给定 URL 是否为浏览器内部页面（如 chrome://, edge:// 等）
 * @param {string} url
 * @returns {boolean}
 */
function isInternalBrowserUrl(url) {
  if (!url) {
    return true;
  }
  const lowered = url.toLowerCase();
  return (
    lowered.startsWith('chrome://') ||
    lowered.startsWith('edge://') ||
    lowered.startsWith('about:') ||
    lowered.startsWith('chrome-extension://')
  );
}

/**
 * 统一处理当前活动标签页：提取 title/url，封装为 JSON 并打印
 * @param {chrome.tabs.Tab} tab
 */
function processActiveTab(tab) {
  if (!tab || !tab.url) {
    return;
  }
  if (isInternalBrowserUrl(tab.url)) {
    return;
  }

  const tabData = {
    timestamp: Date.now(),
    url: tab.url,
    title: tab.title || ''
  };

  // 控制台输出，便于在扩展后台调试抓取逻辑
  console.log('[FocusGuard Extension] 捕获当前标签页:', tabData);

  // 向本地 FocusGuard HTTP 服务上报特征数据
  fetch('http://127.0.0.1:11235/api/tab_update', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(tabData)
  }).catch((err) => {
    // 主程序未启动或本地服务不可达时，静默失败，避免在浏览器后台疯狂报错
    console.debug('FocusGuard 主程序未启动或连接失败');
  });
}

// 监听标签页激活（用户切换到新的标签）
chrome.tabs.onActivated.addListener((activeInfo) => {
  chrome.tabs.get(activeInfo.tabId, (tab) => {
    if (chrome.runtime.lastError) {
      console.warn('[FocusGuard Extension] 获取激活标签页失败:', chrome.runtime.lastError);
      return;
    }
    processActiveTab(tab);
  });
});

// 监听标签页更新（加载完成 / URL 变化）
chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  // 只处理当前活动标签页，避免后台静默标签页干扰
  if (!tab.active) {
    return;
  }

  // 仅在页面加载完成或 URL 发生变化时触发
  const statusChangedToComplete = changeInfo.status === 'complete';
  const urlChanged = typeof changeInfo.url === 'string';

  if (!statusChangedToComplete && !urlChanged) {
    return;
  }

  processActiveTab(tab);
});

