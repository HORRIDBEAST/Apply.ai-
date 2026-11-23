// src/background.ts
// MV3 service worker. The side panel talks to content scripts directly via
// chrome.tabs.sendMessage (it has tabs permission), so this worker's only
// job for now is making the toolbar icon open the side panel instead of a
// popup-style dropdown — chrome.action's default popup (popup.tsx) still
// fires for a plain left-click; this just wires the side panel to also be
// reachable from the action icon on supporting Chrome versions.

export {};

chrome.sidePanel
  ?.setPanelBehavior?.({ openPanelOnActionClick: false })
  .catch((err) => console.error("Failed to set side panel behavior", err));
