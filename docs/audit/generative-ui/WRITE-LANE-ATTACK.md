# Write lane — adversarial pass (2026-08-08)

Four independent attackers drove the REAL modules (real `SurfaceProjector`, real
local-mailbox fixture, real published `input_schema`) against the connector write-back
lane. **All four landed.** 14 findings, 3 critical.

The invariant they all violate is one sentence:

> **The object the user approves must be the object that is sent.**

Today it is not. `StagedRow.changes` is the only thing a human ever sees —
`StageRowView` is `extra="forbid"` and deliberately omits `target_args`, and the
client ledger projection reads `changes` alone — while `target_args` is what
dispatches. Anything that reaches `target_args` without a `changes` entry is invisible.

## Findings

### [CRITICAL] `write_mapping.py:580`

**An op's `required` args are treated as record-addressing scope, so an entire model-authored email body and its recipient are dispatched without ever appearing in the approval diff**

_Repro._ Run /Users/parthpahwa/Documents/work/scratch-attacks/email_value_injection.py (real SurfaceProjector + real local-mailbox fixture + real send_reply schema). The model calls draft_reply(message_id='m-1041', note=<model prose>); the projector mints email://local-mailbox/draft_reply/draft-m-1041 with the model's prose in EmailState.body. The user edits only `subject`. WriteArgScope.for_op computes payload={'subject'} and scope = required - payload = {'to','body'} (write_mapping.py:580), bounded=True because required(3) is a strict subset of properties(4). RowWriteComposer.\_row then composes target_args['to']=edit.row['to'] and target_args['body']=edit.row['body'] (write_mapping.py:745). StagedRow.changes carries the subject diff ONLY. Variant A in email_probe2.py is worse: editing only `cc` makes scope={'to','subject','body'}, so the recipient, subject and the whole model-authored body are all dispatched under a one-line 'cc: "" -> legal@acme.example' diff. The approval gate structurally cannot show any of it: StageRowView (runtime_api/schemas/stages.py:140) is extra="forbid" and omits target_args, packages/chat-surface/src/thread-canvas/ledgerProjection.ts:813 projects only `changes`, and the apply path's only later check (runtime_worker/mcp_effect_executor.py:149 authorize) is a connector-authority re-check with no human-visible argument display.

_Remediation._ Stop equating `input_schema.required` with 'args that address a record'. For any content-bearing write op the required set IS the content, and the strict-subset guard passes for any op with one optional arg. Either (a) require every key in target_args to be reflected in StagedRow.changes — carry unedited scope values as an explicit old==new 'carried' change so the diff the user approves is the full outbound object; or (b) put target_args on StageRowView and make the write gate review the composed args rather than the cell diff. Option (a) preserves the existing wire contract and restores 'the object the user approves is the object that is sent'. A separate declared-identity signal (annotation or catalog entry naming the addressing args) would be the durable fix; `required` alone cannot carry it.

### [CRITICAL] `write_mapping.py:591`

**Payload-lane arg names are never checked against the chosen op's schema, so the model can rename an edited column onto `bcc` (or any arg) — the diff names a field that is not sent and the payload carries a recipient the diff never names**

_Repro._ Compose with candidate `send_email` (properties to/cc/bcc/subject/body/thread_id, required [to,cc,subject]), one edit changing only the `body` cell, and answer args [{arg:'bcc',source:'edited',key:'body'},{arg:'to',source:'row',key:'to'},{arg:'cc',source:'row',key:'cc'},{arg:'subject',source:'row',key:'subject'}]. Coverage passes (bound=={'body'}==edited). `WriteArgScope.for_op` (line 574) builds payload_args from the model-chosen name 'bcc'; `reject_out_of_scope` (line 591) `continue`s on every EDITED binding so the name is never validated against `candidate.input_schema['properties']`. Result: target_args={'bcc':'new body','to':...,'cc':'','subject':...} while StagedRow.changes (line 770) is only [body: 'old body'->'new body']. `StageRowView` carries no target_args, so the user approves 'body changed' and a Bcc goes out. An arg not present in the schema at all ('totally_made_up_arg') is also accepted. Reachable without a jailbreak: `WriteOpCandidate.description` is interpolated verbatim into the mapping prompt at line 884, so a compromised MCP server can steer the binding from its own descriptor.

_Remediation._ In `WriteArgScope.for_op`, additionally require every EDITED binding's `arg` to appear in the chosen candidate's `input_schema['properties']`, and refuse (rather than `continue`) in `reject_out_of_scope` when it does not. Separately, surface the rename: when `binding.arg != binding.key`, the diff the user approves must say `body -> bcc`, not `body`. The durable fix is to stop letting `changes` be an independent display artifact — derive the approval view FROM `target_args` so the two cannot disagree.

### [CRITICAL] `write_mapping.py:739`

**Payload arg NAME is unbounded: the coverage check constrains `binding.key` but never `binding.arg`, so an edit is dispatched into a different connector field than the diff shows**

_Repro._ Compose WriteMappingAnswer(op='update_issue', args=(ArgBinding(arg='description', source=EDITED, key='priority'), ArgBinding(arg='issue_id', source=ROW, key='id'))) against an edit whose only change is priority: high -> low. \_require_exact_coverage (line 704) builds `bound` from binding.key, so edited=={'priority'}==bound and it passes. reject_out_of_scope (line 591) continues on every EDITED binding without inspecting its name. ArgProvenanceAudit passes because 'low' is a value the user typed. Result: target_args == {'description': 'low', 'issue_id': 'PAR-9'} — the connector's description field is overwritten with the string 'low' and the ticket's real description is destroyed, while the approval UI renders only 'priority high -> low'. binding.arg is not even checked against the op's input_schema.properties, so arg='webhook_url' composes identically. The extreme form is ArgBinding(arg='issue_id', source=EDITED, key='priority') alone: WriteArgScope.for_op computes scope = required - payload = {} yet reports bounded=True (line 584), and the write goes out as {'issue_id': 'low'} — the edited cell value has become the record identifier. target_args is never surfaced to the client (runtime_api/schemas/stages.py:143) and stage_commit.py:308 dispatches it verbatim, so nothing between the diff and the connector can catch it.

_Remediation._ Bound the destination the same way the source is bounded. In RowWriteComposer.compose, require every binding.arg to be a key of candidate.input_schema['properties'] — an op with no declared properties should refuse exactly as \_declared_required already refuses. Then extend \_require_exact_coverage to assert on the destination set as well: for an EDITED binding require binding.arg == binding.key unless the connector op declares an explicit column->arg alias, and reject any EDITED binding whose arg appears in \_declared_required(candidate) so a payload binding can never capture a scoping slot. Finally, WriteArgScope.for_op must not report bounded=True when required - payload is empty because payload swallowed every required key.

### [HIGH] `write_mapping.py:591`

**An EDITED binding's target argument name is never validated against the chosen op's schema, so the model can relocate a user-typed value into any argument, including one the connector does not declare**

_Repro._ Variants B and C of /Users/parthpahwa/Documents/work/scratch-attacks/email_probe2.py. WriteArgScope.reject_out_of_scope (write_mapping.py:591) `continue`s on every ArgSourceKind.EDITED binding, and WriteArgScope.for_op derives payload_args from whatever names the model chose, so binding.arg is never compared to candidate.input_schema['properties']. Variant B: the model binds {'arg':'to','source':'edited','key':'subject'} and the user's typed subject value lands in send_reply's `to` argument while the diff reads 'subject: … -> mallory@evil.example'. Variant C: the model binds {'arg':'bcc','source':'edited','key':'subject'} and the composer emits target_args['bcc']='archive@evil.example' even though send_reply declares no `bcc` property at all. In both cases the diff names the SOURCE column and never the destination argument.

_Remediation._ In WriteArgScope.for_op / reject_out_of_scope, require binding.arg to be a declared property of candidate.input_schema for every binding, EDITED included, and refuse an op with no `properties` outright (it already refuses one with no usable `required`). Then surface the arg name alongside the column in the staged diff so a column-to-argument remap is visible rather than implied.

### [HIGH] `write_mapping.py:745`

**Scope-lane args (`ROW` bindings) reach `target_args` with no corresponding `changes` entry, so up to 4 record values — including Cc, Bcc and Reply-To — are dispatched without ever appearing in the diff the user approves**

_Repro._ Candidate `send_email` with required ['to','cc','subject'] (a strict subset of properties, so `WriteArgScope` calls it bounded). User edits one cell (`body`). Answer binds arg 'cc' with source 'row' and key 'reply_to'. `_row` line 745 assigns target_args['cc'] = edit.row['reply_to'] = 'legal-archive@attacker.example'; line 770 sets changes = edit.changes = only the body diff. Wire view: {"changes":[{"field":"body",...}]} — no cc. Same works for `bcc` and `reply_to` when the connector marks them required; I reached four hidden args in one write (to/cc/bcc/reply_to all = 'x@evil.example'). The `required`-as-allow-list heuristic assumes required args are record ADDRESSING keys; for a send-class op every required arg is content, so the bound is vacuous. The model also chooses which row field feeds each scope arg, which is value selection from the record's field menu, not name mapping — I redirected `to` from alice@ to ceo@ with the diff showing only a body change.

_Remediation._ Every arg composed into `target_args` must have a user-visible counterpart. Add a `context: tuple[RowContextArg, ...]` to `StagedRow`, populate it from the ROW bindings, expose it on `StageRowView`/`packages/api-types` and render it in the approval UI as 'also sending: to=…, cc=…'. Additionally, tighten scope: an op whose required set contains no id-like key (nothing matching the read op's identity field / the surface's row key) should refuse as UNBOUNDED_OP rather than treating content args as addressing keys.

### [HIGH] `write_mapping.py:616`

**One optional property defeats the `required < properties` discrimination test, restoring the full-record overwrite**

_Repro._ Build WriteOpCandidate(name='update_issue', input_schema={'properties': {id, priority, assignee, state, notes, idempotency_key}, 'required': ['id','priority','assignee','state','notes']}) and a WriteMappingAnswer binding priority=EDITED plus ROW bindings for id/assignee/state/notes. One SurfaceRowEdit with a single change priority 'high'->'low'. RowWriteComposer.compose() returns a StagedRow whose `changes` is one line and whose `target_args` is {'priority':'low','id':'PAR-9','assignee':'alice','state':'open','notes':'high'} — five fields, four of them never in the diff. Dropping `idempotency_key` from `properties` (so required == properties) flips the SAME answer to a UNBOUNDED_OP refusal, proving the single optional key is the whole difference. The class docstring claims an everything-required schema 'has said nothing about which args address a record'; a schema that marks all-but-one required has said nothing either, yet passes, because the guard is a proper-subset test rather than a test of how much the declaration actually narrows.

_Remediation._ Stop treating `required` as a scope allow-list on the strength of a proper-subset test. Require the declaration to genuinely discriminate — e.g. refuse unless `len(required) <= _Limits.MAX_SCOPE_ARGS` outright (an op needing more than 4 args to ADDRESS a record is already the case the module says it refuses), or unless `required` is a small fraction of `properties`. Better still, subtract from `scope` every arg that also appears as a surface COLUMN: a field the user can see and edit is a payload field, not a record address, so a name in both sets should never ride along unedited. Then a connector whose op genuinely requires a body field is handled by echoing the value as read only when the arg is not a rendered column.

### [HIGH] `write_mapping.py:595`

**Scope is enforced on `binding.arg` only — the model freely chooses which row field's VALUE fills an in-scope arg**

_Repro._ Honest narrow schema: WriteOpCandidate(input_schema={'properties': {id, priority, assignee, notes}, 'required': ['id','notes']}). Row as read: {'id':'PAR-9','priority':'high','assignee':'alice','notes':'ship by friday'}. User edits one cell: priority 'high'->'low'. Answer binds ArgBinding(arg='notes', source=ROW, key='assignee'). `reject_out_of_scope` only asks whether 'notes' is in scope_args — it is — and never compares `key` to `arg`, so composition assigns target_args['notes'] = edit.row['assignee'] = 'alice'. ArgProvenanceAudit passes because 'alice' is a real value in the row. The staged row's `changes` still shows only the priority line, so the approval gate reports a one-cell edit while the dispatched write silently overwrites the notes field with the assignee's name. The same trick with key='priority' sends the value the user just edited AWAY from — exactly the `notes='high'` relocation the module docstring names as harm #2 of the original bug.

_Remediation._ Make the ROW lane a correspondence, not a free mapping: require `binding.key == binding.arg` for ArgSourceKind.ROW (a scoping key is the record's own field under the connector's own name), or carry an explicit connector-authored arg->field map and validate the pair against it. If a genuine rename is needed, it must come from the connector descriptor, not from the model. Add a composition test asserting that a ROW binding whose key names a different column than its arg is refused.

### [HIGH] `write_mapping.py:745`

**ROW binding's value source is unbounded: a declared scope arg can be filled from any field of the client-supplied row, swapping which record the write addresses**

_Repro._ WriteArgScope constrains binding.arg for a ROW binding to the connector's declared-required set, but binding.key is free and is resolved by a flat edit.row[key] lookup. With row = {'id': 'PAR-9', 'parent_id': 'PAR-1', 'priority': 'high'} and ArgBinding(arg='issue_id', source=ROW, key='parent_id'), composition yields target_args == {'issue_id': 'PAR-1', 'priority': 'low'}: the staged row is titled 'Fix the login redirect' (PAR-9) and its diff describes PAR-9, but the write lands on PAR-1. Provenance passes because 'PAR-1' really was in the row. The same shape drains any field padded into row that was never a rendered column — ArgBinding(arg='issue_id', source=ROW, key='\_internal_token') produced {'issue_id': 'sk-live-xyz', 'priority': 'low'}, moving a non-column value into a connector argument. This falsifies the claim at write_back.py:41-44 that padding row with extra fields widens nothing: it does not widen the arg NAME set, but the value is what identifies the record.

_Remediation._ Require a ROW binding to be identity-mapped: binding.key == binding.arg (or resolved through a connector-declared mapping), so a declared scope arg can only ever be filled from the identically-named field as read. Additionally restrict the resolvable key space to the columns the surface actually rendered — carry the surface's column list into SurfaceRowEdit and reject a ROW key that is not one of them, rather than accepting any key the client put in row.

### [HIGH] `stage_rowset_write.py:115`

**`stage_rowset_write` still accepts model-authored `target_args` with no relation to the displayed `changes` — the pre-fix signature, live behind the surfaces_v2 flag**

_Repro._ The tool's input contract takes rows: tuple[StagedRow, ...] straight from model output, and RowsetValidator.validate (surfaces_v2/rowset.py:131) checks only caps, unique row keys and hold references — there is no provenance audit, no WriteOpCandidate, and no cross-check between changes and target_args. Executing StageRowsetWriteInput.model_validate({'target_connector':'linear','target_op':'update_issue','title':'Lower the priority on PAR-9','rows':[{'row_key':'PAR-9','title':'Fix the login redirect','changes':[{'field':'priority','old':'high','new':'low'}],'target_args':{'issue_id':'PAR-1','description':'','assignee':'mallory','state':'cancelled'}}]}) is ACCEPTED, and RowsetValidator accepts it too. The user sees one line, priority high -> low; the dispatcher sends a four-field overwrite against a different issue. The tool is constructed unconditionally whenever settings.execution.surfaces_v2 is on (runtime_worker/handlers/run.py:2138 and 2165); REVIEWED_ROWSET_TARGETS narrows the connector/op pair but places no constraint on the argument values inside it.

_Remediation._ Route this lane through the same two gates the user-save lane uses: fetch the op descriptor for the reviewed target and enforce WriteArgScope against its input_schema, and require every leaf of target_args to be accounted for by the row's own changes (each change.new must appear as the value of exactly one arg, and every other arg must be a connector-declared required scoping key). At minimum reject any row where target_args contains a key that no change.field maps to and that is not in the op's required set, so the diff the user approves and the args the dispatcher sends cannot describe different writes.

### [MEDIUM] `email_surface.py:336`

**EmailDraftSurface flattens a recipient list into one truncated, comma-joined string, hiding recipients past the eighth from the only surface a human reviews them on**

_Repro._ EmailDraftSurface.match({'to': [f'r{i}@acme.example' for i in range(12)], 'subject':'s','body':'b'}) yields state['to'] == 'r0@… , … r7@acme.example +4 more' — r8..r11 are absent from EmailState entirely and the original list survives only in EmailDraftMatch.id_basis, which is never shipped as state.data. The composer is the only place a person inspects the recipients before Send, and four of them are not there. Under the write lane above, `to` is a scope arg sourced from edit.row['to'], so the value dispatched is the literal string 'r0@…, …, r7@… +4 more' — neither the real recipient set nor anything the user could have typed. Separately, \_address_of (email_surface.py:325) falls back to a display `name` when the object carries no address key, and \_addresses_of joins with ', ' (line 175), so a connector- or model-authored display name containing a comma injects an extra address into the single To value: match({'to':[{'name':'Jordan Reyes <jordan@acme.example>, mallory@evil.example'}], …}) renders and would dispatch both.

_Remediation._ Keep the recipient list in EmailState as a list and let EmailRenderer elide it visually with an expandable control, so nothing the user is about to send is unreachable from the surface; never let a '+N more' summary become a value a write can source. Drop the name/displayname fallback in \_address_of, or normalise it: a slot that is not a parseable address should fail the match rather than become one, and any recipient string containing the separator should be rejected.

### [MEDIUM] `write_back.py:340`

**`SurfaceRowEdit.row` is client-supplied and never cross-checked against the server-held surface snapshot, so a `ROW` binding can pull a value the surface never rendered**

_Repro._ `SurfaceWriteBackCoordinator._origin` folds the ledger to recover the surface's connector and read op, but never compares `edits[*].row` to the surface's actual projected data. Post a write-back whose `row` carries `secret_recipient: 'nobody-saw-this@evil.example'` plus a one-cell body edit, and bind arg 'cc' source 'row' key 'secret_recipient'. It composes: target_args={'body':'new body','to':...,'cc':'nobody-saw-this@evil.example','subject':...}. The write_back module docstring asserts 'It is the row as the surface rendered it', and `SurfaceRowEdit`'s docstring repeats it, but nothing enforces it — the ArgProvenanceAudit's row half is whatever the caller says it is.

_Remediation._ Resolve the row from the server-held surface snapshot (the projection `_origin` already loads) and use that as the provenance source, accepting only `row_key` + `changes` from the client. If the client-supplied row must stay, intersect it with the snapshot's rendered fields before `ArgProvenanceAudit.for_edit` and before any ROW binding can read it.

### [MEDIUM] `write_mapping.py:729`

**Two changes on the same field in one row: the diff renders both, only the last is sent, and no validator rejects the duplicate**

_Repro._ by_column = {change.field: change for change in edit.changes} is a dict comprehension, so a later change on the same field silently replaces the earlier one. With changes = (RowFieldChange(field='priority', old='high', new='low'), RowFieldChange(field='priority', old='high', new='urgent')), composition produced target_args == {'issue_id': 'PAR-9', 'priority': 'urgent'} while the StagedRow's changes still carries BOTH lines, so the approval UI shows the user 'priority high -> low' as an accepted change that will never be sent. EditBatchValidator.validate (line 784) checks emptiness, the row cap, duplicate row keys and the per-row change cap, but not field uniqueness; RowsetValidator.validate (surfaces_v2/rowset.py:144) has the same gap. WriteMappingAnswer.\_args_are_distinct closes the mirror case (two bindings on one arg) but not this one.

_Remediation._ Add a field-uniqueness rule to EditBatchValidator.validate: within one SurfaceRowEdit reject when len({c.field for c in edit.changes}) != len(edit.changes), with a constant safe message alongside the existing \_Messages. Mirror it in RowsetValidator.validate so the staging engine refuses the same shape when it arrives from the agent lane, matching the reasoning already written for \_args_are_distinct.

### [LOW] `write_mapping.py:729`

**Duplicate `changes` entries for one field are accepted; the diff renders both while only the last is sent**

_Repro._ Send changes = [cc: ''->'oversight@acme.example', cc: ''->'', body: 'old body'->'new body']. `EditBatchValidator` (line 784) and `RowsetValidator` both only cap the count, never dedupe the field. `by_column = {change.field: change for change in edit.changes}` at line 729 collapses to the last, so target_args['cc']='' while `StagedRow.changes` still ships both entries to the UI. The approval view therefore displays a cc value that will not be sent.

_Remediation._ Reject duplicate `change.field` within one `SurfaceRowEdit` in `EditBatchValidator.validate` (and in `RowsetValidator` for the agent-authored lane), with a typed rejection rather than a silent last-wins collapse.

### [LOW] `write_mapping.py:584`

**An op whose declaration is ruled unusable still stages a write when the answer happens to carry no ROW binding**

_Repro._ WriteOpCandidate(input_schema={'properties': {...}}) with no `required` key at all. WriteArgScope.for_op yields bounded=False, scope_args=frozenset(). An answer of a single EDITED binding (arg='priority', key='priority') never enters the `binding.source is not EDITED` branch, so reject_out_of_scope returns without looking at `bounded`, and compose stages target_args={'priority':'low'} — a write dispatched against an op the module has just declared it cannot bound, and with no record-addressing argument at all. The class docstring states such an op 'is refused ... as is one that declares no schema at all', but the refusal is conditional on the shape of the model's answer rather than on the op.

_Remediation._ Refuse on the op, not on the binding: in RowWriteComposer.compose, raise \_Messages.UNBOUNDED_OP as soon as `WriteArgScope.for_op(...).bounded` is False, before composing any row. That makes the documented invariant unconditional and removes the reliance on the model happening to emit a ROW binding.

## Guards that DID hold — refuted attacks, kept so nobody re-litigates them

- a `literal` binding is refused (INVENTED_VALUE)
- a `row` binding to a field absent from `edit.row` is refused
- a `row` binding to a non-required arg is refused
- an everything-is-required schema is refused as UNBOUNDED_OP
- "select-bound to a path the payload lacks" is unreachable for email: the rung is
  deterministic (`projector.py:657`), its paths are host-authored constants, zero model calls

## Attack probes

The four probes live outside the repo at `~/Documents/work/scratch-attacks/`.
They are the regression suite for any fix: each must REJECT after remediation.
