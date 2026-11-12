// Issue #10: describing an element must not crash when its `value` is not a string.
// <li>.value is a number (0 by default), so `value.substring` threw after the click.
const test = require('node:test');
const assert = require('node:assert/strict');
const { loadContentScript } = require('./helpers/content-script');

const PAGE = `
  <ul><li id="item" role="button">List item</li></ul>
  <input id="name" value="hello">`;

test('click on a <li> reports success and describes the element', async () => {
  const { window, send } = loadContentScript(PAGE);
  window.document.getElementById('item').addEventListener('click', () => { window.clicked = 'li'; });
  const result = await send({ action: 'click', selector: '#item' });

  assert.equal(window.clicked, 'li');
  assert.equal(result.error, undefined); // failures come back as {success:false, error}
  assert.equal(result.clicked, true);
  assert.equal(result.element.tag, 'li');
  assert.equal(result.element.value, null);
});

test('page info lists <li role=button> elements without crashing', async () => {
  const { window, send } = loadContentScript(PAGE);
  // jsdom lays nothing out; page info skips zero-size elements.
  window.document.getElementById('item').getBoundingClientRect =
    () => ({ left: 0, top: 0, width: 80, height: 20 });
  const info = await send({ action: 'getPageInfo' });

  assert.equal(info.success, undefined, info.error); // errors come back as {success:false}
  assert.ok(info.interactiveElements.some((el) => el.tag === 'li' && el.value === null));
});

test('string values are still reported and truncated', async () => {
  const { send } = loadContentScript(`<input id="long" value="${'x'.repeat(300)}">`);
  const result = await send({ action: 'getElements', selector: '#long' });

  assert.equal(result.elements[0].value, 'x'.repeat(200));
});
