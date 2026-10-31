# T04d inventory: every control on the author surface

Measured 2026-10-31 on the stand (API 127.0.0.1:8010, database muse_e2e) with the seeded admin session. "Dead" means the endpoint the control calls answers 404/405/409/400-"cannot" for every input the control can send, or the control does nothing when clicked.

Columns: screen | control | label now | what it does | endpoint + method | precondition (disabled when, reason shown) | states | leads next | defect.

Defect legend: U unlabelled (no label, no help line, or the reason is only a tooltip) | R raw identifier on screen | D dead | L unlinked | S missing state | M money/count not in words.

Rows below carry the defect found BEFORE the fix. Section "After the fix" at the end repeats the result: the defect column is empty there for every control that was fixed; what could not be fixed says why.

## Measurements that decided the "dead" rows

| probe | answer | verdict |
| --- | --- | --- |
| `POST /invites/<token>/revoke` with the token the create call returned | 404 `invite not found` (the route takes the invite id; create returns no id) | Revoke on a new link was DEAD |
| `PUT /roles/administrator` | 400 `the administrator role must keep its permissions` | Edit on administrator is DEAD |
| `DELETE /roles/administrator` | 400 `the administrator role cannot be deleted` | Delete on administrator is DEAD |
| `POST /sources/upload?media=pdf` with non-PDF bytes | 400 `unreadable PDF upload` (error shown by the form) | live, error state exists |
| `POST /sources/upload?media=image` | 201, source with 0 passages, status ready | live; "No passages read yet" must be shown, not a bar |
| `POST /sources/upload?media=audio` then the queued transcription | 201, then operation `failed`: `transcription failed with status 404` (no model key on the stand) | live; failure must reach the source row and the bell |
| `GET /human-queue` | 200, open items with `payload.course_id` | a URL that "waits for a person" had NO screen to resolve it (S/L) |
| `GET /courses/<id>/plan/validate` | 200 with `tree_errors` strings that name internal ids | never printed; shown as a count only |
| `GET /courses` | rows have no `scope_tags` | invite -> course link needs `GET /courses/<id>` per course |
| plan block `source_refs` | only `{handle: "P1"}`, no source id, no passage | a handle can link to the Sources tab, not to the exact row (backend gap, see end) |

## Shell and bell

| screen | control | label now | does | endpoint | precondition | states | leads next | defect |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| shell | brand text | Sourcebound | nothing | none | none | n/a | nowhere | L |
| shell | signed-in text | Signed in as Ada Admin | shows the name | GET /auth/me | none | loading/error: sign-in | nowhere | L |
| shell | language select | (aria only) Language | switches UI locale | none | none | n/a | stays | U |
| shell | theme button | Dark / Light (names the target, not the action) | toggles theme | none | none | n/a | stays | U |
| shell | sign out | Sign out | ends session | POST /auth/logout | none | error toast | /signin | none |
| shell | nav links | My courses, Users, Roles, Invites | navigate | none | permission decides if listed | n/a | pages | none |
| bell | bell button | (aria only) Open notifications; count badge | opens panel, marks read | POST /notifications/<id>/read | none | loading, empty, error | panel | U |
| bell | panel heading | Notifications (no help line) | none | GET /notifications | none | empty line has no next step | none | U, S |
| bell | operation row | `Kind: echo` + chip `done` | shows progress | GET /operations/<id> | none | queued/done/failed | nowhere | R, L |
| bell | notification row | Operation finished / 1 new | shows server history | GET /notifications | none | empty | nowhere | L |
| bell | run test button | Run a test operation | POST echo | POST /operations/echo -> 202 | needs operation.run, hidden otherwise | queued toast, bell | bell row | U (purpose unclear) |

## Sign-in, waiting, invite accept, not found

| screen | control | label now | does | endpoint | precondition | states | leads next | defect |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| /signin | page | Sourcebound + tagline + "Sign in" | explains | none | none | n/a | | none |
| /signin | Continue with Google | link | GET /auth/login?provider=google (302) | | none | n/a | Google | none |
| /waiting | Check again | Check again | reload session | GET /auth/me | none | busy, "Still no access." | / or /signin | none |
| /waiting | Sign out | Sign out | POST /auth/logout | | none | | /signin | none |
| /invite/:token | Sign in and accept | link | login with next | GET /auth/login | signed out | | back here | none |
| /invite/:token | accept (automatic) | Invitation accepted + tags | POST /invites/<token>/accept | | signed in | loading, error, done | / | L (tags not words, no course link for the author, no way back) |
| /invite/:token | Open the app | link | navigates | none | | | / | none |
| not found | Open the app | link | navigates | none | | | / | U (title says "Nothing here yet.") |

## Courses list

| screen | control | label now | does | endpoint | precondition | states | leads next | defect |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| /courses | page title | My courses (no sentence) | none | GET /courses | none | loading/empty/error/denied | | U |
| /courses | New course | New course | opens dialog | POST /courses | disabled without course.create, reason shown as `Needs the permission course.create.` (raw code) | | plan of new course | R |
| /courses | dialog Title | Title + hint | input | | | | | none |
| /courses | dialog Language | Language (no help line) | select | | | | | U |
| /courses | dialog Scope tags | Scope tags + hint | input | | | | | none |
| /courses | dialog Create | Create | submits | POST /courses 201 | disabled while title empty, NO reason shown | busy, error | /courses/<id>/plan | U |
| /courses | dialog Cancel | Cancel | closes | | | | | none |
| /courses | course link | title | opens plan | | | | /courses/<id>/plan | none |
| /courses | status chip | Draft | | | | | | none |
| /courses | Needs now text | `Needs now: answer the open questions` plain text | none | | | | nowhere | L |
| /courses | empty state | You have no courses yet. (no action) | | | | empty | | S |

## Course header (all four tabs)

| screen | control | label now | does | endpoint | precondition | states | leads next | defect |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| course header | title | course title | | GET /courses/<id> | | loading/error/denied | | none |
| course header | breadcrumbs | none | | | | | | L |
| course header | one sentence | none | | | | | | U |
| course header | Needs now | plain text | | GET plan/validate | | | nowhere | L |
| course header | tab links | Sources, Plan, What to fix, Publish (no counts) | navigate | | | | tabs | L (no counts) |

## Sources tab

| screen | control | label now | does | endpoint | precondition | states | leads next | defect |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| sources | section | Add a source + hint | | | | | | none |
| sources | File | File input (no example) | choose file | | | | | U |
| sources | Kind of file | select, options text/markdown/pdf/image/audio | | | | | | U (no help) |
| sources | Licence | text + hint | | | | | | none |
| sources | As of | text + hint | | | | | | none |
| sources | Upload | Upload | multipart | POST /sources/upload 201 | disabled without a file or source.create, NO reason | busy, error text | row in candidates | U |
| sources | Web address | text + hint | | | | | | none |
| sources | Add address | Add address | fetch | POST /sources/fetch 201 | disabled while empty, NO reason | busy, "waits for a person" | candidates | U |
| sources | added list | label + UUID chip + "Waits for a person" | | | | | nowhere | R, L |
| sources | waits-for-a-person URL | no screen to approve or dismiss | | GET /human-queue, POST /human-queue/<id>/resolve | needs source.approve | | | S, L |
| sources | Approve | Approve | | POST /sources/<id>/approve | disabled without source.approve, NO reason | busy, toast | library | U |
| sources | Reject | Reject | | POST /sources/<id>/reject | same | | library | U |
| sources | candidate row | title + media chip raw (`text`) + licence | | | | | no link to source/passages | R, L |
| sources | library | heading "Approved sources" but it also lists rejected ones | | GET /courses/<id>/sources | | empty | | U |
| sources | ingest state | not shown (queued/parsing/failed/ready) | | | | | | S |
| sources | passages | "2 passages read" | | | | | | none |

## Plan tab

| screen | control | label now | does | endpoint | precondition | states | leads next | defect |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| plan | Save plan | Save plan | PATCH whole set + base version | PATCH /courses/<id>/plan | disabled while nothing is staged, NO reason | busy, 409 bar | refreshed tree | U |
| plan | tree | module/topic nodes | select, drag | none | | loading/empty/error | centre panel | none |
| plan | tree toggle | aria "Show or hide the topics of X" | | | | | | none |
| plan | Title field | Title (no help) | | | | | | U |
| plan | Rename | Rename | stages | none (local) | disabled when unchanged, NO reason | | | U |
| plan | Move up / Move down | Move up, Move down | stages | none (local) | NOT disabled at the ends: click does nothing | | | D |
| plan | Delete | Delete | opens confirm | none (local) | | confirm names object | | none |
| plan | blocks list | role, raw type chip (`text`, `scenario_practice`), raw handle chips (`P1`) | | | | | handles go nowhere | R, L |
| plan | constraints | `audience_level`, kind chip `audience`, JSON answer | | | | empty line | | R |
| plan | question input | label = question text, hint | | | | | | none |
| plan | Answer | Answer | stages | none | disabled while empty, NO reason | | | U |
| plan | Estimate | heading "Forecast, not a limit", line `About 6 blocks, about $0.124500` | | GET /courses/<id>/estimate | | loading/error | | U, M |
| plan | Generate course | Generate course | POST generate/submit -> 202 | | disabled on questions/dirty/permission; reason is wrong when the plan has errors | busy, then "Open the run" | /runs/<id> | U |
| plan | Open the run | link | | | | | run | none |
| plan | Build the plan | methodologies as free text codes (hint names codes that do not exist) | POST profile + plan/submit | POST /courses/<id>/profile, POST plan/submit 202 | disabled while empty, NO reason | busy, error, no queued line, plan not refreshed on completion | plan | R, U, S |
| plan | plan problems | `tree_errors` never shown, Generate has no reason for them | | GET plan/validate | | | | S |

## What to fix tab

| screen | control | label now | does | endpoint | precondition | states | leads next | defect |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| fix | section | "What to fix" with no help line | | GET /courses/<id>/held | | loading/empty/error | | U |
| fix | batch button | `Rewrite 3 held blocks, about $0.062250` | | POST /courses/<id>/held/rewrite 202 | disabled without course.edit, NO reason | busy, toast, bell | | M, U, S |
| fix | topic title | bold text | | | | | nowhere | L |
| fix | Rewrite | Rewrite | | POST /blocks/<id>/rewrite 202 | no reason | busy, queued | refresh on completion missing | U, S |
| fix | Edit | Edit | opens brief editor | none | | | | U |
| fix | Save / Cancel | Save, Cancel | PATCH brief | PATCH /blocks/<id> | | busy | | none |
| fix | Accept as checked by me | | | POST /blocks/<id>/accept | no reason | busy | row leaves | U |
| fix | passage + address | quote + `para1` | | | | | not linked to the source | L |

## Publish tab

| screen | control | label now | does | endpoint | precondition | states | leads next | defect |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| publish | section | Publish (no help line) | | | | | | U |
| publish | published note | `Published version <uuid>` | | | | | | R |
| publish | Publish course | Publish course | confirm dialog | POST /courses/<id>/publish 201 | enabled even with held blocks (the backend then refuses with 422) | error text in dialog | | U, D-ish |
| publish | dialog Publish / Cancel | | | | | | | none |
| publish | invites block | embedded invite form and list | | see Invites | | | | see Invites |

## Run page

| screen | control | label now | does | endpoint | precondition | states | leads next | defect |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| run | page title | Generation run (no sentence, no breadcrumbs, no link to the course) | | GET /runs/<id> | needs run.start | loading/empty/error/denied | | U, L |
| run | status + line | `Working on block:t1-a.` raw step key | | polls every 3 s | | | | R |
| run | cost | `Spent so far: 0.12. Forecast: 1.00.` no currency | | | | | | M |
| run | topic rows | title + chip | | | | | not linked to held blocks | L |
| run | step rows | raw `block:t1-a` + chip + error | | | | | | R |
| run | Resume | Resume | | POST /runs/<id>/resume 202 | disabled unless stopped, reason only as one hint line | busy | | U |
| run | Cancel run | Cancel run | | POST /runs/<id>/cancel | disabled unless running, NO reason | busy | | U |

## Users

| screen | control | label now | does | endpoint | precondition | states | leads next | defect |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| users | page title | Users (no sentence) | | GET /users | needs user.list | loading/empty/error/denied | | U |
| users | search | placeholder + aria | filters, URL | none | | "Nothing matches" | | none |
| users | table | Name, Email, Status, Created, Actions; sort + paging in URL | | | | | | none |
| users | roles | not shown although `roles` is in the response | | | | | | L |
| users | Assign role | Assign role | dialog | POST /roles/<name>/assign | disabled without role.assign; reason in tooltip only | | | U |
| users | Ban | Ban | dialog | POST /users/<id>/ban | disabled without user.ban or when banned; reason in tooltip only, none for banned | | | U |
| users | Delete | Delete | dialog | DELETE /users/<id> | disabled without user.delete; tooltip only | backend refusal shown | | U |
| users | dialog fields | Role, Scope tags (+hint), Reason (+hint), Heir (+hint), legacy checkbox | | | | | | none |
| users | dialog submit | Assign role / Ban / Delete | | | disabled while invalid, NO reason | busy, error | | U |

## Roles

| screen | control | label now | does | endpoint | precondition | states | leads next | defect |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| roles | page title | Roles (no sentence) | | GET /roles | needs role.edit | | | U |
| roles | New role | New role | dialog | POST /roles 201 | tooltip reason | | | U |
| roles | table | Name, "1 permissions", scope, record matrix raw (`course: read/write`) | | | | | holders not shown | R, M, L |
| roles | Edit | Edit | dialog | PUT /roles/<name> | administrator answers 400 | | | D (administrator) |
| roles | Delete | Delete | dialog | DELETE /roles/<name> 204 | administrator answers 400 | | | D (administrator) |
| roles | permission checkboxes | raw codes (`section.courses`) | | GET /permissions has label + description | | | | R |
| roles | submit | Create / Save / Delete | | | disabled while name empty, NO reason | | | U |

## Invites (page and Publish tab)

| screen | control | label now | does | endpoint | precondition | states | leads next | defect |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| invites | page title | Invite links + intro | | GET /invites | needs invite.create | loading/empty/error | | none |
| invites | Course tags / Seats / Expires | fields with hints | | | | | | none |
| invites | Create invite | Create invite | | POST /invites 201 | disabled while no tag or no permission, NO reason | busy, error | new link row | U |
| invites | new link row | link, uses, expiry, tag chips raw | | | | | course not shown | L |
| invites | Copy link | Copy link | clipboard | none | | toast | | none |
| invites | Revoke | Revoke | POST with the token | POST /invites/<token>/revoke | 404 for every new link | | | D |
| invites | existing rows | uses + tags + Revoked chip | | GET /invites | | empty | no revoke, no course | L, D-ish |

## After the fix


Defect column after the fix is EMPTY for every row of the tables above except the rows listed under "Not fixed". Each row maps to an audit item (build/notes/T04d-audit.md) like this; the check that holds it is named.

| audit item | control change | held by |
| --- | --- | --- |
| A1 front door | list shows "You have N courses", one-line page help, Create your first course in the empty state | t04d-states empty, walk |
| A2 Needs now is text | every "Needs now" is a link to an anchor (`#build-plan`, `#question-<key>`, `#source-<id>`, `#held-<id>`, `#publish-open`, `#generate`) | walk "Needs now" test |
| A3 scope tags in New course | field removed; the tag is derived (`c-<8 hex>`) and never shown | walk dialogs |
| B1, B2, B3 course page | stage strip Sources, Plan, Generate, What to fix, Publish with one state each (done, needs you, blocked with the reason, not yet), counts, breadcrumb, one next-action card | walk tab counts |
| C1, C2, C3 plan | open questions on one card each with its own Save answer; constraints replaced by "Answered questions" (question text, answer) | screens plan tests |
| C4 estimate | section "Estimate", help "A forecast, not a limit.", "$0.12" | walk raw text |
| C5, C8 reasons and dirty state | GuardedButton everywhere: a disabled button always has its reason next to it; "Unsaved changes" chip sits in the Save row | walk disabled-without-reason |
| C6 methodologies | pick list with name and one line, level of thinking select | states queued plan |
| C7 tree | handles link to the Sources list; module node shows purpose and topics; Move up/down disabled at the ends | walk |
| D1, D4, D5 sources | list first, state per row (waiting to be read, being read, read, could not be read + message), Add source drawer with "or" between file and address, addresses waiting for a person can be approved or dismissed | walk, states |
| D3 | kind detected from the file name; licence and as-of hints | walk dialogs |
| E1, E2 fix | "Nothing to fix yet: generate the course first" with link; one word "held" | states fix empty |
| F1, F2, F3 publish | summary first, Publish disabled with the reason while blocks are held, invites only on /invites with the course preselected | screens publish tests |
| G1 users | Roles column (chips link to /roles#role-name), no Ban/Delete on your own account, reasons visible | walk |
| G2 invites | course picker, rows show course, uses, expiry, Revoke invite (by id, fixes the 404) | mutations invites test |
| G3 roles | record matrix removed, "Held by" column, built-in administrator Edit/Delete disabled with the reason | walk administrator test |
| G4 tags | picked by course name on invites; role scope field explained | walk dialogs |
| H1 chrome | icons in the nav, "This course" section | |
| H2, H3, H4 | "Switch to dark theme"; test operation removed from the bell; PrimeVue 4.5.5 MIT, no licence host | walk licence test |
| H5 | h1 / h2 / hint sizes differ; axe contrast still green in both themes | a11y spec |

### Not fixed (and why)

| row | why |
| --- | --- |
| D2 native file input shows the browser locale | a styled picker was not built in this pass; the label and hint are ours |
| B1 language, audience and topic counts on the course page | not added; the page shows title, stage strip and next action |
| C7 add module or topic | the plan edit endpoint has no add operation (rename, move, delete only); needs a backend op |
| plan handle -> exact source row and passage | `source_refs` carries only `{handle}`; links go to the Sources list. Needs `source_id` and passage address in the plan response |
| methodologies list | no `GET /methodologies`; the four codes live in `composables/methodologies.ts` with names in i18n |
| invite creator, who accepted, invite preview before accepting | no such fields or route in the API |
| New course as a drawer | kept as a dialog; the Add source form is the drawer |
| audio upload on a stand without a model key | upload is accepted, the transcription operation fails with 404 from the model API; shown honestly as "Could not be read" with the message |
| scope tags on roles | still typed by hand (explained in the hint) |
