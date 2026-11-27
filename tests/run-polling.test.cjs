const test = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {runInNewContext} = require('node:vm');
const source = readFileSync(join(__dirname, '../web/app.js'), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));
const snapshot = (id,status) => ({run:{run_id:id,status},progress:{run_id:id,status}});

function fixture(fetchRun) {
  const timers = new Map();
  let sequence = 0;
  const app = runInNewContext(source + `
    api=fetchRun; updateRunPanels=()=>{}; toast=()=>{}; currentView=()=>'home';
    ({state,selectProject,restoreRun})`, {
    fetchRun, AbortController, document:{addEventListener(){}},
    setTimeout(fn,delay){timers.set(++sequence,{fn,delay});return sequence;},
    clearTimeout(id){timers.delete(id);}
  });
  return {...app,timers};
}

test('restoring a running analysis reuses its poll and recovers from a temporary failure', async () => {
  let calls=0;
  const app=fixture(async path=>{
    assert.equal(path,'/api/runs/r1');
    if (++calls===1) throw Object.assign(new Error('temporarily unavailable'),{status:503});
    return snapshot('r1','succeeded');
  });
  app.selectProject('A');
  app.restoreRun(snapshot('r1','running'));
  await flush();
  assert.match(app.state.pollNotice,/自动重连/);
  app.restoreRun(snapshot('r1','running'));
  assert.equal(calls,1);
  const [timer]=app.timers.values();
  assert.equal(timer.delay,1500);
  await timer.fn();
  assert.equal(calls,2);
  assert.equal(app.state.run.status,'succeeded');
  assert.equal(app.state.pollNotice,'');
});

test('late completion from a previous project cannot replace the current project progress', async () => {
  let finish;
  const app=fixture(()=>new Promise(resolve=>{finish=resolve;}));
  app.selectProject('A');
  app.restoreRun(snapshot('old','running'));
  app.selectProject('B');
  app.restoreRun(null);
  finish(snapshot('old','succeeded'));
  await flush();
  assert.equal(app.state.run,null);
  assert.equal(app.state.runId,null);
  assert.equal(app.timers.size,0);
});
