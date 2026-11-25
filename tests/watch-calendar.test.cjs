const test = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {runInNewContext} = require('node:vm');
const source = readFileSync(join(__dirname,'../web/watch.js'),'utf8');
const {watchedMilestones,watchCalendar,milestoneDate} = runInNewContext(source+
  '\n({watchedMilestones,watchCalendar,milestoneDate})',{TextEncoder});

test('retain month precision and use last observed date to identify schedule slips',()=>{
  const row={id:'NCT00000001',title:'测试,一;二\n'+ '长标题'.repeat(30),updated:'2026-02-01',
    fields:{'主要完成日期':'2026-04-03 · 预计','研究完成日期':'2026-05 · 预计'},
    changes:[{fields:[{name:'主要完成日期',before:'2026-03 · 预计'},{name:'研究完成日期',before:'2026-05-15 · 预计'}]}]};
  const events=watchedMilestones([row]);
  assert.equal(events[0].status,'预计延期');
  assert.equal(events[1].status,'预计');
  const file=watchCalendar(events,new Date('2026-02-01T00:00:00Z'));
  const unfolded=file.replaceAll('\r\n ','');
  assert.match(unfolded,/DTSTART;VALUE=DATE:20260403/);
  assert.match(unfolded,/SEQUENCE:1/);
  assert.ok(unfolded.includes('测试\\,一\\;二\\n'));
  assert.equal((file.match(/BEGIN:VEVENT/g)||[]).length,1);
  assert.ok(file.split('\r\n').every(line=>Buffer.byteLength(line)<=75));
  row.fields['主要完成日期']='2026-04-03 · 实际';
  assert.equal(watchedMilestones([row])[0].status,'实际');
  assert.equal(milestoneDate('2026-02-30 · 预计'),null);
});
