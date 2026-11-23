// src/contents/scanner.ts
// Content script — the only context with real DOM access. The side panel
// and background worker can't touch the page directly, so they message
// this script to (a) scan the page's HTML for the backend's
// FormUnderstandingAgent and (b) fill fields once answers come back.

import type { PlasmoCSConfig } from "plasmo";
import { fillFields, type FillResult } from "~lib/dom-injector";
import type { DetectedField } from "~lib/types";

export const config: PlasmoCSConfig = {
  matches: ["<all_urls>"],
  run_at: "document_idle"
};

export type ContentMessage =
  | { type: "SCAN_PAGE" }
  | { type: "FILL_FIELDS"; fields: Array<{ field: DetectedField; value: string }> };

export type ContentResponse =
  | { type: "SCAN_RESULT"; html: string; pageUrl: string }
  | { type: "FILL_RESULT"; results: FillResult[] };

chrome.runtime.onMessage.addListener(
  (message: ContentMessage, _sender, sendResponse) => {
    if (message.type === "SCAN_PAGE") {
      const response: ContentResponse = {
        type: "SCAN_RESULT",
        html: document.documentElement.outerHTML,
        pageUrl: window.location.href
      };
      sendResponse(response);
      return true;
    }

    if (message.type === "FILL_FIELDS") {
      const results = fillFields(message.fields);
      const response: ContentResponse = { type: "FILL_RESULT", results };
      sendResponse(response);
      return true;
    }

    return false;
  }
);
