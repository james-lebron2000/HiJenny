const test = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {runInNewContext} = require('node:vm');
const app = readFileSync(join(__dirname,'../web/app.js'),'utf8');
const check = runInNewContext(app+'\ncheckOcrNote',{document:{addEventListener(){}}});
const doc = {document_id:'doc1',sha256:'a'.repeat(64),pdf_review_ranges:{scan:[[2,4]]}};
const note = {document_id:'doc1',project_id:'p1',registered_sha256:doc.sha256,page:3,
              reviewed_text:'128',ocr_draft:'12B',reviewed_at:'2026-11-27T12:00:00Z',
              reviewer:'test',outcome:'reviewed',language:'eng'};

test('read both reviewed and illegible records from the matching PDF',()=>{
  check(note,doc,'p1');
  check({...note,outcome:'illegible',reviewed_text:null},doc,'p1');
});

test('reject records that do not match the page, source, scope or review outcome',()=>{
  for (const edit of [{document_id:'doc2'},{project_id:'p2'},{registered_sha256:'b'.repeat(64)},
                      {page:1},{reviewer:' '},{outcome:'illegible'},{reviewed_text:null}]) {
    assert.throws(()=>check({...note,...edit},doc,'p1'));
  }
});
