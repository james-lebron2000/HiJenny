const test = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { runInNewContext } = require('node:vm');

test('each pilot locale labels every canonical quote option and cites the same sample numbers',()=>{
  const source = readFileSync(join(__dirname,'../web/pilot.js'),'utf8');
  const {pilotCopy,pilotChoices} = runInNewContext(source+'\n({pilotCopy,pilotChoices})');
  for (const lang of ['zh','en','ja']) {
    const copy=pilotCopy[lang];
    assert.deepEqual(Object.keys(copy).sort(),Object.keys(pilotCopy.zh).sort());
    for (const [key,values] of Object.entries(pilotChoices)) {
      assert.equal(new Set(copy.choices[key]).size,values.length);
      assert.ok(copy.choices[key].every(Boolean));
    }
    for (const number of ['42.0%','48.4%','53/126','61/126']) assert.ok(copy.figures.includes(number));
  }
});
