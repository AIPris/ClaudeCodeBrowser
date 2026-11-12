// Issue #6: browser_upload_file. The background script fetches each file from the
// server's single-use URL and hands the bytes to the page's top frame.
const test = require('node:test');
const assert = require('node:assert/strict');
const { loadBackgroundScript } = require('./helpers/background-script');
const { loadContentScript } = require('./helpers/content-script');

const TAB = { id: 4, windowId: 1, url: 'https://makerworld.com/publish' };
const FILES = [
  { url: 'http://127.0.0.1:8765/upload/aaa', name: 'cover.png', type: 'image/png' },
  { url: 'http://127.0.0.1:8765/upload/bbb', name: 'model.zip', type: 'application/zip' },
];

function setup({ failUrl } = {}) {
  const fetched = [];
  const sent = [];
  const fetch = async (url) => {
    fetched.push(url);
    if (url === failUrl) return { ok: false, status: 404 };
    return { ok: true, arrayBuffer: async () => new TextEncoder().encode(`bytes of ${url}`).buffer };
  };
  const tabs = {
    query: async () => [TAB],
    get: async () => TAB,
    sendMessage: async (tabId, message, options) => {
      sent.push({ message, options });
      return { uploaded: message.files.length };
    },
  };
  return { bg: loadBackgroundScript({ tabs }, { fetch }), fetched, sent };
}

test('fetches every file and sends the bytes to the top frame', async () => {
  const { bg, fetched, sent } = setup();

  const result = await bg.handleCommand({ action: 'uploadFile', tabId: TAB.id,
    data: { selector: '#file', files: FILES } });

  assert.equal(result.success, true, result.error);
  assert.equal(result.uploaded, 2);
  assert.deepEqual(fetched, FILES.map((f) => f.url));
  assert.equal(sent.length, 1);
  const { message, options } = sent[0];
  assert.deepEqual({ ...options }, { frameId: 0 });
  assert.equal(message.action, 'setFiles');
  assert.equal(message.selector, '#file');
  assert.deepEqual(Array.from(message.files, (f) => [f.name, f.type]), [['cover.png', 'image/png'], ['model.zip', 'application/zip']]);
  assert.equal(new TextDecoder().decode(message.files[0].bytes), `bytes of ${FILES[0].url}`);
});

test('a file the server no longer serves is reported by name', async () => {
  const { bg, sent } = setup({ failUrl: FILES[1].url });

  const result = await bg.handleCommand({ action: 'uploadFile', tabId: TAB.id,
    data: { selector: '#file', files: FILES } });

  assert.equal(result.success, false);
  assert.match(result.error, /model\.zip/);
  assert.equal(sent.length, 0);
});

test('the content script refuses a target that is not a file input', async () => {
  const { send } = loadContentScript('<button id="drop">Drop files here</button>');
  const result = await send({ action: 'setFiles', selector: '#drop', files: [{ name: 'a.png', type: 'image/png', bytes: new ArrayBuffer(1) }] });
  assert.equal(result.success, false);
  assert.match(result.error, /input type="file"/);
});

test('several files need an input with the multiple attribute', async () => {
  const { send } = loadContentScript('<input type="file" id="one">');
  const two = ['a.png', 'b.png'].map((name) => ({ name, type: 'image/png', bytes: new ArrayBuffer(1) }));
  const result = await send({ action: 'setFiles', selector: '#one', files: two });
  assert.equal(result.success, false);
  assert.match(result.error, /multiple/);
});
