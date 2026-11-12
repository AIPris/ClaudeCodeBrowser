// Issue #7: full_page screenshots returned the page dimensions instead of an image,
// and the server then crashed on `'dict' object has no attribute 'startswith'`.
const test = require('node:test');
const assert = require('node:assert/strict');
const { loadBackgroundScript } = require('./helpers/background-script');

const PAGE = { scrollHeight: 3029, scrollWidth: 1180, viewportHeight: 801, viewportWidth: 1180 };
const TAB = { id: 5, windowId: 1, url: 'http://127.0.0.1/', title: 'Test' };

function fakeTabs() {
  const calls = [];
  const tabs = {
    query: async () => [TAB],
    get: async () => TAB,
    update: async () => TAB,
    sendMessage: async (tabId, msg) => (msg.action === 'captureFullPage' ? PAGE : {}),
    captureVisibleTab: async (windowId, opts) => {
      calls.push(['captureVisibleTab', windowId, opts]);
      return 'data:image/png;base64,VISIBLE';
    },
    captureTab: async (tabId, opts) => {
      calls.push(['captureTab', tabId, opts]);
      return 'data:image/png;base64,FULLPAGE';
    },
  };
  return { tabs, calls };
}

test('full-page screenshot returns an image of the whole document', async () => {
  const { tabs, calls } = fakeTabs();
  const bg = loadBackgroundScript({ tabs });

  const result = await bg.takeScreenshot(TAB.id, { fullPage: true });

  assert.equal(result.success, true, result.error);
  assert.equal(result.type, 'fullPage');
  assert.equal(result.data, 'data:image/png;base64,FULLPAGE');
  const capture = calls.find(([name]) => name === 'captureTab');
  assert.deepEqual({ ...capture[2].rect }, { x: 0, y: 0, width: 1180, height: 3029 });
});

test('visible screenshot is unchanged', async () => {
  const { tabs } = fakeTabs();
  const bg = loadBackgroundScript({ tabs });

  const result = await bg.takeScreenshot(TAB.id, {});

  assert.equal(result.success, true, result.error);
  assert.equal(result.type, 'visible');
  assert.equal(result.data, 'data:image/png;base64,VISIBLE');
});
