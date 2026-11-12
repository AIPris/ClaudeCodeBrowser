// Issue #3: execute_script rejected top-level return/await, leaked const/let into
// the next call, failed on relative fetch URLs and could not reach page globals.
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const { loadBackgroundScript } = require('./helpers/background-script');

const TAB = { id: 5, windowId: 1, url: 'http://127.0.0.1:8799/index.html' };

// Stands in for one tab: a content-script sandbox that keeps its globals between
// executeScript calls (as Firefox does), plus the page's own JS world behind
// window.wrappedJSObject.
function fakeTab({ csp = false } = {}) {
  const fetched = [];
  // The page's own global object, as window.wrappedJSObject gives it.
  const page = vm.createContext({ pageGlobal: 42, JSON });
  if (csp) {
    // A Content-Security-Policy without 'unsafe-eval' (issue #32).
    page.eval = () => { throw new Error('call to eval() blocked by CSP'); };
  }
  const sandbox = vm.createContext({
    location: { href: TAB.url },
    URL,
    Promise,
    JSON,
    window: {
      fetch: async (url) => { fetched.push(url); return { status: 200, json: async () => ({ ok: true }) }; },
      wrappedJSObject: page,
    },
  });
  sandbox.window.location = sandbox.location;
  const tabs = {
    query: async () => [TAB],
    get: async () => TAB,
    // Firefox: compile errors reject, the last statement's value is returned,
    // and a Promise result is awaited.
    executeScript: async (tabId, { code }) => [await vm.runInContext(code, sandbox)],
  };
  return { tabs, fetched };
}

async function run(bg, script, extra = {}) {
  const result = await bg.executeScript(TAB.id, { script, ...extra });
  return JSON.parse(JSON.stringify(result)); // plain objects from this realm
}

test('last expression value is returned, as before', async () => {
  const bg = loadBackgroundScript(fakeTab());
  assert.deepEqual(await run(bg, 'const a = 2; a * 21'), { success: true, result: 42 });
});

test('top-level return works', async () => {
  const bg = loadBackgroundScript(fakeTab());
  assert.deepEqual(await run(bg, 'const t = "title"; return t.toUpperCase();'), { success: true, result: 'TITLE' });
});

test('top-level await works and the promise is resolved', async () => {
  const bg = loadBackgroundScript(fakeTab());
  const result = await run(bg, 'const v = await Promise.resolve(7); return v + 1;');
  assert.deepEqual(result, { success: true, result: 8 });
});

test('const/let do not leak into the next call', async () => {
  const bg = loadBackgroundScript(fakeTab());
  assert.equal((await run(bg, 'const w = 1; w')).result, 1);
  assert.deepEqual(await run(bg, 'const w = 2; w'), { success: true, result: 2 });
});

test('relative fetch URLs resolve against the page', async () => {
  const tab = fakeTab();
  const bg = loadBackgroundScript(tab);
  const result = await run(bg, "const r = await fetch('/data.json'); return r.status;");
  assert.deepEqual(result, { success: true, result: 200 });
  assert.deepEqual(tab.fetched, ['http://127.0.0.1:8799/data.json']);
});

test('real errors are still reported', async () => {
  const bg = loadBackgroundScript(fakeTab());
  const result = await run(bg, 'nope.missing');
  assert.equal(result.success, false);
  assert.match(result.error, /nope is not defined/);
});

test('world "page" works on pages whose CSP forbids eval', async () => {
  const bg = loadBackgroundScript(fakeTab({ csp: true }));
  assert.deepEqual(await run(bg, 'pageGlobal * 2', { world: 'page' }), { success: true, result: 84 });
  assert.deepEqual(await run(bg, 'const v = await Promise.resolve(1); return pageGlobal + v;', { world: 'page' }),
    { success: true, result: 43 });
});

test('world "page" reaches page globals and returns JSON-safe values', async () => {
  const bg = loadBackgroundScript(fakeTab());
  assert.deepEqual(await run(bg, '({ g: pageGlobal })', { world: 'page' }), { success: true, result: { g: 42 } });
  assert.deepEqual(await run(bg, 'return pageGlobal + 1;', { world: 'page' }), { success: true, result: 43 });
  assert.deepEqual(await run(bg, 'const v = await Promise.resolve(2); return pageGlobal + v;', { world: 'page' }),
    { success: true, result: 44 });
});
