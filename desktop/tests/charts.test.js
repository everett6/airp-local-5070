import assert from 'node:assert/strict';
import test from 'node:test';
import { barChart } from '../public/charts.js';

test('zero-allocation scenario bars render finite coordinates', () => {
  const html = barChart(['flat'], [{ name: 'cash only', y: [0] }]);
  assert.ok(html.includes('<svg'));
  assert.ok(!html.includes('NaN') && !html.includes('Infinity'));
});
