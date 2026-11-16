# GitHub Projects v2 research for Sahir-AI

**Date:** 16 November 2026 (Australia/Sydney, UTC+11)  
**Scope:** Research written before setup; see the outcome note below.  
**Target:** Public repo [mhaiprojects/Sahir-AI](https://github.com/mhaiprojects/Sahir-AI), owned by the personal user account `mhaiprojects` (not an organization).  

> **Outcome (16 Nov 2026):** the Project was created as "MH Voice Assistant" (since renamed [Sahir](https://github.com/users/mhaiprojects/projects/1), along with the repo, now `Sahir-AI`), linked to this repo, and made **public** (the maintainer chose a public board over the private option recommended below). It uses Status (Todo / In Progress / Done), Priority (P0–P2), Size (S/M/L), Area and Target date fields, with Table, Board and Roadmap views. Because the board is public, it holds no ClickUp IDs, estimates or private notes. As an experiment, task tracking for this project has moved to GitHub Issues on this board; ClickUp stays in use for other work.

## Summary and recommendation

Keep **ClickUp as the source of truth** for private planning (one task per feature). Use a **user-owned GitHub Project** as a complement for this public repo: a board of GitHub issues and pull requests, auto-added from `Sahir-AI`, so code-level work sits next to the code.

Do not replace ClickUp. The native ClickUp GitHub integration links commits, branches, and pull requests by task ID, but it does not sync GitHub Projects fields. Paid two-way sync tools also do not treat a Project as a sync target. Start the Project **private** even though the repo is public, so estimates, ClickUp IDs, and internal notes stay off the public internet. Convert drafts to issues only when a task is ready to be public.

Before any CLI setup, the maintainer needs `gh auth login` (browser or SSH) with the `repo`, `read:org`, and `project` scopes.

## 1. Creating a user-owned Project and linking it to the repo

A Project is owned by a user or an organization. It is not "inside" a repository, but it can be **linked** to one and can have a **default repository**. Visibility of the Project is independent of the repo: a public repo can have a private Project, and the other way around.

The Project URL will look like `https://github.com/users/mhaiprojects/projects/<number>`.

### 1.1 Create it in the UI

From the [quickstart](https://docs.github.com/en/issues/planning-and-tracking-with-projects/learning-about-projects/quickstart-for-projects):

1. Click the profile picture, then **Your profile**.
2. Open the **Projects** tab, then **New project**.
3. Start from scratch (**Table**, **Board**, or **Roadmap**) or pick a built-in template. Review the fields, views, workflows, and insights if a template is used.
4. Name it, for example `Sahir`.
5. Click **Create project**. (The organization flow can also import existing repo items and set a default repository in this dialog. The user-project flow does not show that import checkbox; set the default repository afterwards.)

Optional: add a short description and a Markdown README under **… → Settings**.

CLI equivalent :

```bash
gh project create --owner mhaiprojects --title "Sahir"
```

### 1.2 Link it to Sahir-AI

You can only list Projects owned by the same user or organization that owns the repository. For this repo, that means a Project owned by `mhaiprojects`.

From the [linking docs](https://docs.github.com/en/issues/planning-and-tracking-with-projects/managing-your-project/adding-your-project-to-a-repository):

1. Open [https://github.com/mhaiprojects/Sahir-AI](https://github.com/mhaiprojects/Sahir-AI).
2. Click **Projects**, then **Link a project**.
3. Search for the new Project and click it.

Set the **default repository** so new issues created from the Project (and drafts converted to issues) land in this repo, and so the Project also appears on the repo's Projects tab:

1. Open the Project → **… → Settings**.
2. Under **Default repository**, choose `mhaiprojects/Sahir-AI`.
3. **Save changes**.

CLI:

```bash
gh project link 1 --owner mhaiprojects --repo Sahir-AI
```

(`1` is a placeholder for the Project number returned by `gh project create` or `gh project list`.)

### 1.3 Public vs private (separate from the repo)

From the [visibility docs](https://docs.github.com/en/issues/planning-and-tracking-with-projects/managing-your-project/managing-visibility-of-your-projects):

- **Public:** anyone on the internet can view the Project (board, views, field values).
- **Private:** only people granted at least read access can see the Project.
- Visibility of an *item* still depends on the repository. A private issue or PR stays hidden even on a public Project.
- Change it later under **… → Settings → Danger zone → Visibility**.

The owner of a user Project, and collaborators with admin permission, can change visibility and invite people with Read, Write, or Admin roles under **Settings → Manage access**. That invitation is for the Project only, not for the repo. See [Managing access to your projects](https://docs.github.com/en/issues/planning-and-tracking-with-projects/managing-your-project/managing-access-to-your-projects).

**Recommendation for this repo:** start **private**. The repo is already public, so issues and PRs can still be public. A public Project would also publish Priority, Size, Estimate, Iteration, and any ClickUp IDs or notes stored as fields. Make the Project public later only if a public roadmap is wanted, and keep internal fields off it.

The GraphQL API can also set this with `updateProjectV2(..., public: false)` ([API docs](https://docs.github.com/en/issues/planning-and-tracking-with-projects/automating-your-project/using-the-api-to-manage-projects)).

## 2. Issues vs draft items

A Project holds three kinds of item: **draft issues**, **issues**, and **pull requests**. A Project can hold up to **50,000 items** across active views and the archive. See [Adding items to your project](https://docs.github.com/en/issues/planning-and-tracking-with-projects/managing-items-in-your-project/adding-items-to-your-project).

### 2.1 Draft issues

Drafts live only in the Project. They are good for capturing an idea before it should appear on the public issue tracker. They can have a title, body, assignees, and custom Project fields. They cannot have repository labels or milestones until converted. Assignees and @mentions on a draft do **not** get notifications until it becomes an issue.

Create one by typing in the bottom row of the table and pressing Enter, or with `gh project item-create`.

### 2.2 Converting a draft to an issue

From [Converting draft issues to issues](https://docs.github.com/en/issues/planning-and-tracking-with-projects/managing-items-in-your-project/converting-draft-issues-to-issues):

1. Click the context menu on the draft (table or board layout).
2. Choose **Convert to issue**.
3. Pick the repository (the default repository, if set, is the natural target).

The new issue is public if the repo is public. Drafts cannot be converted into pull requests.

### 2.3 Adding issues and pull requests

Ways to add existing issues or PRs:

- Paste the URL into the bottom row.
- Type `#` and search a repository.
- **Add item from repository** (bulk, with filters such as `label:bug`).
- From the repo's Issues or Pull requests list: select items → **Projects**.
- From an issue or PR sidebar → **Projects**.
- Command palette (`Ctrl+K`) → "Add items".
- Auto-add workflow (section 5).
- `gh project item-add` or `gh issue create --project "…"`.

Adding, removing, or changing the **Status** field of an issue or PR writes a timeline event on that item, visible to people who have at least read access to the Project. Built-in workflow changes are attributed to `@github-project-automation`.

You can also create a real issue from the Project with **Create new issue**. If the current view is grouped by a field, the new issue inherits that group's value (for example Status = Todo).

### 2.4 Sub-issues

From [Adding sub-issues](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/adding-sub-issues): an issue can have up to **100 sub-issues** and **eight** nesting levels. Parent issue and sub-issue progress can be shown as Project fields, used to group or filter (`parent-issue:"mhaiprojects/Sahir-AI#12"`). These are repository issues, not Project-only drafts.

CLI:

```bash
gh issue create --repo mhaiprojects/Sahir-AI --title "…" --body "…" --parent 12
gh issue edit 12 --repo mhaiprojects/Sahir-AI --add-sub-issue 13
```

### 2.5 Issue types

[Issue types](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/managing-issue-types) work on **user accounts** as well as organizations. The account owner can manage them under profile **Settings → Planning → Issue types**. Defaults are **task**, **bug**, and **feature**. Up to 25 types. They appear on issues and can be used in Project filters and views. They classify issues; they do not replace sub-issues.

```bash
gh issue create --repo mhaiprojects/Sahir-AI --title "…" --type Feature
```

### 2.6 Pull requests as items

PRs are first-class Project items. Built-in workflows can set Status to Done when a PR is merged. Linked PRs are a built-in field on issues. Use a board or table filter such as `is:pr` if you want a PR-only view.

## 3. Views: table, board, and roadmap

Each saved view is a tab. Layout, grouping, sort, filter, and slice-by are stored when you **Save changes**. Unsaved edits show a dot on the tab and are visible only to you. See [Changing the layout of a view](https://docs.github.com/en/issues/planning-and-tracking-with-projects/customizing-views-in-your-project/changing-the-layout-of-a-view) and [Managing your views](https://docs.github.com/en/issues/planning-and-tracking-with-projects/customizing-views-in-your-project/managing-your-views).

| Layout | What it is | Good for |
|---|---|---|
| **Table** | Spreadsheet of items and fields | Backlog, bulk edits, grouping by Priority |
| **Board** | Kanban columns from a single-select or iteration field (Status by default). Dragging updates the field. | Day-to-day status |
| **Roadmap** | Timeline from custom date and/or iteration fields. Drag to change start/target dates or iteration. Optional vertical markers for iterations, milestones, and item dates. | Planning across weeks |

Create a view with **New view** (saved immediately). Duplicate, rename, reorder tabs (drag), or delete from the **View** menu. Change layout under **View → Layout**.

### Grouping, sorting, filtering, slice-by

Documented for the table in [Customizing the table layout](https://docs.github.com/en/issues/planning-and-tracking-with-projects/customizing-views-in-your-project/customizing-the-table-layout) and [Filtering projects](https://docs.github.com/en/issues/planning-and-tracking-with-projects/customizing-views-in-your-project/filtering-projects):

- **Group by** a field (not title, labels, reviewers, or linked pull requests). Dragging an item into a group sets that field.
- **Sort** by one or more fields; click again to reverse.
- **Slice by** a field to open a side panel of values; clicking a value filters the current view (cannot slice by title, reviewers, or linked PRs).
- **Field sum** can total a number field (for example Estimate) per group or board column.
- **Filter** from the search bar or `Ctrl+K` → "Filter by". Multiple filters are AND. Multiple values on one field (`label:bug,docs`) are OR. There is no OR across different fields.
- Useful qualifiers: `assignee:@me`, `label:bug`, `status:Todo`, `milestone:"…"`, `no:assignee`, `has:priority`, `priority:High`, `iteration:@current`, `date:@today`, `is:issue`, `is:open`. Comparisons: `>`, `>=`, `<`, `<=`, `..`, and `@previous` / `@current` / `@next` for iterations.
- On a board, clicking a badge (for example an assignee) also filters.

Insights (charts) are available from the Project menu and are optional; see [About Projects](https://docs.github.com/en/issues/planning-and-tracking-with-projects/learning-about-projects/about-projects).

## 4. Fields

A Project can have up to **50 fields**, including built-in metadata and custom fields.

### 4.1 Built-in fields that come from the issue or PR

These stay in sync automatically. Changing them in the Project changes the issue or PR, and the other way around. They are **not** set with the Project field mutation; use the issue/PR APIs (or the UI) instead.

| Field | Notes |
|---|---|
| **Assignees** | People on the issue or PR. Grouping by assignee lets you reassign by dragging. |
| **Labels** | Repo labels. You cannot group a view by labels. |
| **Milestone** | Repo milestone. |
| **Reviewers** | PRs only. |
| **Linked pull requests** | Shown on issues. Cannot group or slice by this field. |
| **Repository** | Useful if the Project later spans more than one repo. |
| **Title**, **Type** (issue type) | Title is always visible. Type is the issue type from section 2.5. |
| **Parent issue**, **Sub-issue progress** | Enable from hidden fields if you use sub-issues. |

**Status** is a built-in Project single-select (typical options: Todo, In Progress, Done). It is the field the default workflows update. Options can be renamed or extended in Project settings. Changing Status writes a timeline event on the issue or PR.

**Iteration** is a first-class field type, usually added as a custom field named Iteration. It is listed here because GitHub treats it as a standard planning field, not because it exists before you create it.

### 4.2 Custom fields to add

Create from the rightmost table header → **New field**, or with `gh project field-create` (see section 7). Types:

| Type | Use | Limits and notes |
|---|---|---|
| **Single select** | Priority, Size, or any fixed list | Up to 50 options, each with color and description. Can have a default. Filter as `priority:High`. [Docs](https://docs.github.com/en/issues/planning-and-tracking-with-projects/understanding-fields/about-single-select-fields) |
| **Number** | Estimate (points or hours) | Filter with `estimate:>=3` or `estimate:1..5`. Can have a default. Supports field sum. [Docs](https://docs.github.com/en/issues/planning-and-tracking-with-projects/understanding-fields/about-text-and-number-fields) |
| **Date** | Start date, target date | Needed for a useful roadmap. Format `YYYY-MM-DD`. No default values. [Docs](https://docs.github.com/en/issues/planning-and-tracking-with-projects/understanding-fields/about-date-fields) |
| **Text** | Notes, ClickUp task ID | Freeform; filter with exact text. Can have a default. |
| **Iteration** | Repeating sprints | Any length, supports breaks, individually editable. Creating the field auto-creates three iterations. Filter with `iteration:@current`. Group and bulk-move unfinished work to the next iteration. [Docs](https://docs.github.com/en/issues/planning-and-tracking-with-projects/understanding-fields/about-iteration-fields) |

Suggested fields for this Project (plan only):

- **Priority** — single select: `High`, `Medium`, `Low`
- **Size** — single select: `XS`, `S`, `M`, `L`, `XL`
- **Estimate** — number (story points or hours; pick one and stick to it)
- **Iteration** — weekly or fortnightly, created in the UI (`gh project field-create` cannot create ITERATION; its `--data-type` is only `TEXT`, `SINGLE_SELECT`, `DATE`, `NUMBER`)
- **ClickUp ID** — optional text field, for example `<task-id>`, so the two systems can be cross-referenced without syncing
- Optional dates: **Start date** and **Target date** if a roadmap view is wanted

## 5. Automations

### 5.1 Built-in workflows

Configure under the Project menu → **Workflows**. See [Using the built-in automations](https://docs.github.com/en/issues/planning-and-tracking-with-projects/automating-your-project/using-the-built-in-automations).

Enabled by default when a Project is created:

- When an issue or PR in the Project is **closed**, set Status to **Done**.
- When a PR in the Project is **merged**, set Status to **Done**.

Others to turn on:

- **Item added to project** → set Status to **Todo** (issues and/or PRs).
- Close an issue when its Project Status is set to Done (optional; useful, but only if Status is the real source of "finished").
- Reopened items can be moved back from Done (configure if needed).

Built-in workflows can set **Status**. They cannot set Priority, Size, Estimate, Iteration, or other custom fields. There is no built-in "roll unfinished items into the next iteration" workflow, but you can bulk-move a group in a view grouped by Iteration.

### 5.2 Auto-add from the repo, with a filter

[Adding items automatically](https://docs.github.com/en/issues/planning-and-tracking-with-projects/automating-your-project/adding-items-automatically):

1. **Workflows → Auto-add to project → Edit**.
2. Choose the repository (`mhaiprojects/Sahir-AI`).
3. Add a filter, then **Save and turn on workflow**.

Supported filter qualifiers: `is` (`open`, `closed`, `merged`, `draft`, `issue`, `pr`), `label`, `reason` (`completed`, `reopened`, `"not planned"`), `assignee`, `no` (`label`, `assignee`, `reason`). All except `no` can be negated (`-label:wontfix`).

**Forward-only:** items that already exist when you enable the workflow are not added. Bulk-add the current backlog once by hand (or with `gh`).

Each auto-add workflow watches one repository. You can duplicate it, with a unique filter, up to the plan cap. Templates and `gh project copy` do **not** copy auto-add workflows; set them again on the copy.

**Plan limits (auto-add workflows per Project):**

| Plan | Maximum auto-add workflows |
|---|---|
| GitHub Free | **1** |
| GitHub Pro | 5 |
| GitHub Team | 5 |
| GitHub Enterprise Cloud / Server | 20 |

`mhaiprojects` is a personal user account. On **GitHub Free**, this Project gets **one** auto-add workflow, which is enough if it only watches `Sahir-AI`. A typical filter is `is:issue,pr` (everything new) or `is:issue,pr -label:wontfix`. If the maintainer is on Pro, up to five filters are available (for example one for issues and one for PRs).

### 5.3 Auto-archive

[Archiving items automatically](https://docs.github.com/en/issues/planning-and-tracking-with-projects/automating-your-project/archiving-items-automatically) uses only `is`, `reason`, and `updated` (`<@today-14d`, `<@today-3w`, `<@today-1m`). Unlike auto-add, it **also archives items that already match**. Archived items keep their field values and can be restored. Archiving does not free the 50,000-item cap; deleting does.

A sensible starting filter: `is:closed,merged updated:<@today-1m`.

### 5.4 GitHub Actions and the GraphQL API

For anything the built-in workflows cannot do (extra auto-add on Free, set Priority from a label, and so on):

- GraphQL mutations such as `addProjectV2ItemById`, `addProjectV2DraftIssue`, and `updateProjectV2ItemFieldValue`. See [Using the API to manage Projects](https://docs.github.com/en/issues/planning-and-tracking-with-projects/automating-your-project/using-the-api-to-manage-projects). Tokens need `read:project` to query and `project` to mutate.
- [actions/add-to-project](https://github.com/actions/add-to-project/) (GitHub-maintained) adds the current issue or PR to a Project, with optional label filters (`AND` / `OR` / `NOT`). `project-url` looks like `https://github.com/users/mhaiprojects/projects/1`. It needs a PAT (or fine-grained token) with `repo` and `project` scopes, stored as a secret. It does not support Projects (classic).
- A workflow file that calls `gh api graphql`, as in [Automating Projects using Actions](https://docs.github.com/en/issues/planning-and-tracking-with-projects/automating-your-project/automating-projects-using-actions).

**Important:** `GITHUB_TOKEN` is scoped to the repository and **cannot** access Projects. For a user-owned Project, GitHub recommends a **personal access token** (classic) with `project` and `repo`, stored as a repository secret. A GitHub App is the recommended path for organization Projects.

A workflow lives in one repository, so for a multi-repo Project you would add the file to each repo. That does not apply while this Project only tracks `Sahir-AI`.

The `projects_v2_item` webhook can also drive automation on a server you control.

## 6. ClickUp integration: replace or complement?

the maintainer already manages work in **ClickUp** (a task per feature). GitHub Projects should complement that, not replace it.

### 6.1 What ClickUp's GitHub integration actually does

From [ClickUp's GitHub integration help](https://help.clickup.com/hc/en-us/articles/6305771568791-GitHub-integration) (available on every ClickUp plan):

- A **Workspace connection** (owners/admins) attaches GitHub repos and links them to ClickUp Spaces. A **personal connection** (members and above) is needed to create branches, PRs, and issues from a task. Guests and limited members cannot use the integration. Only repository admins can add repos to ClickUp; only Workspace owners/admins link those repos to Spaces.
- Once a Space is linked to a repo, ClickUp auto-associates new GitHub activity if a valid task ID appears in the **commit message, branch name, PR title, or PR description**. Formats: `#taskID`, `CU-taskID`, or a custom ID such as `ENG-123` (and `#ENG-123`). Example: `#<task-id>` or `CU-<task-id>`.
- To **update ClickUp status** from GitHub, append the status in square brackets with **no space**: `#<task-id>[ready]`.
- When a task ID is in a PR, ClickUp posts a comment on the PR with a link to the task (from the GitHub account that set up the repo/Space mapping).
- You can also paste a GitHub link onto a task, and you can create a GitHub issue, branch, or PR from the task (personal connection required). Branch name format is configurable; the default is `:taskId:_:taskName:_:username:`.
- Linked commits, branches, PRs, and issues show in the task's GitHub panel and activity feed. A Pull Requests column can be added to List or Table views.
- The integration's OAuth scopes include `read:user`, `repo`, and `admin:repo_hook`. `project` and `read:org` are listed as optional "when enabled" for projects and boards data. That is ClickUp's access to GitHub, not a two-way Projects sync.

It does **not** keep GitHub Projects fields (Status, Priority, Estimate) in sync with ClickUp statuses or custom fields. It is activity linking, not a board mirror.

[ClickUp GitHub Automations](https://help.clickup.com/hc/en-us/articles/6305836061463-Use-GitHub-Automations) (triggers such as PR merged, commit, CI status, plus a "Create GitHub issue" action) are available on **Business plans and above**, with the Automation ClickApp enabled. That is a plan limit to be aware of; this research did not check which ClickUp plan the maintainer is on.

### 6.2 Can Projects replace ClickUp for this repo?

No, not as a full replacement of how the maintainer uses ClickUp today.

| | ClickUp | GitHub Projects |
|---|---|---|
| Strengths | Already the planning system; rich tasks, lists, docs, cross-project work; native Git activity on each task | Lives next to the public code; issues, PRs, and Status stay in sync; public contributors can use issues; free; `gh` + Actions |
| Gaps | No Project-board sync; Automations need Business; GitHub is a sidecar | Weaker docs, time tracking, and cross-product planning; a public Project would leak internal fields |

Replacing ClickUp would mean re-homing every feature task and losing the existing Space/List structure. There is no benefit that outweighs that, especially while this is still largely a solo repo.

### 6.3 Recommended pattern

**ClickUp remains the source of truth. GitHub Issues + a private user-owned Project handle code-level work for the public repo.**

Practical rules:

1. Keep feature planning, acceptance notes, and estimates in ClickUp .
2. When work is ready to happen in the public repo, open a GitHub issue (or convert a Project draft) and put it on the Project board. Mention the ClickUp task ID in the issue body and, optionally, in a **ClickUp ID** text field.
3. Put the ClickUp task ID in branch names, commit messages, and PR titles (`#<task-id>` or `CU-<task-id>`) so ClickUp's native integration attaches the Git activity automatically. Use `#<task-id>[status]` only when you deliberately want ClickUp's status to change.
4. Let the Project's built-in workflows move items to Done when the issue is closed or the PR is merged. Do not try to make ClickUp Status and Project Status the same field.
5. Keep the Project **private** so ClickUp IDs and estimates are not a public roadmap unless the maintainer later wants that.

### 6.4 Sync options, if tighter coupling is ever needed

| Option | What it does | Verdict |
|---|---|---|
| **Native ClickUp GitHub integration** | Links Git activity and can change ClickUp status from a commit/PR message. No Projects field sync. Free on every ClickUp plan. | **Use this.** It is enough for the recommended pattern. |
| **ClickUp GitHub Automations** | PR/commit/CI triggers that update ClickUp tasks; can create GitHub issues from ClickUp. Business plan and above. | Useful later if the maintainer already has Business. Still not a Projects sync. |
| **Zapier or Make** | Event-driven, usually one-way zaps (issue opened → create ClickUp task, and the reverse). Easy to get loops or missed updates. Does not treat GitHub Projects as a first-class board. | Fine for a single "new issue → ClickUp task" rule. Poor as a full two-way system. |
| **Unito** | Persistent two-way sync of GitHub **issues and PRs** with ClickUp tasks (status, comments, assignees, labels, and so on). [Unito explicitly does not support GitHub Projects](https://guide.unito.io/limitations-github-integration) as a block of work; issues that happen to be on a Project still sync as issues. Sub-issues and some comment edits are also limited. | Only if issue↔task parity becomes important. It will not sync the Project board itself. |
| **Custom script** | ClickUp API plus `gh project item-add` / `item-edit` or GraphQL. Full control, including Project fields. You own the mapping, retries, and loop prevention. | Worth it only if a specific field (for example ClickUp status → Project Status) becomes painful to maintain by hand. |

**Recommendation, with pros and cons:** adopt the complement pattern above and turn on the native ClickUp GitHub link for the relevant ClickUp Space. Pros: no new paid tool, no dual backlog to reconcile, public issues stay clean, private planning stays in ClickUp, and Git history already points at task IDs. Cons: Status will sometimes disagree until a PR is merged or someone updates one side; there is a little manual issue-opening when a ClickUp task becomes repo work. That is acceptable for a single public repo. Revisit Unito or a small script only if contributor-facing issues become frequent and the two lists drift.

## 7. `gh` CLI

### 7.2 Login and scopes 

The minimum scopes `gh` requests on login are `repo`, `read:org`, and `gist`. Project commands need the **`project`** scope as well ([gh project](https://cli.github.com/manual/gh_project)). Adding an issue to a Project with `gh issue create --project` also requires `project` ([gh issue create](https://cli.github.com/manual/gh_issue_create)).

Interactive login:

```bash
# First-time login: browser flow, SSH for git, plus the project scope
gh auth login --hostname github.com --git-protocol ssh --web --scopes project

# If gh is already logged in later, add the scope without a full re-login
gh auth refresh -s project

# Confirm
gh auth status
```

`--scopes project` on `gh auth login` is an *additional* scope; `repo` and `read:org` are already in the default set. The interactive `gh auth login` (no flags) also works: choose GitHub.com, HTTPS or SSH, and authenticate in the browser, then run `gh auth refresh -s project`.

For headless or Actions use, a classic PAT needs `repo` and `project` (and `read:org` if you want the usual `gh` behaviour). `read:project` is enough for queries only. Fine-grained tokens must grant Projects access for the user account and the repo.

### 7.3 Commands, with examples for this repo

Placeholder: Project number `1` and title `Sahir`. Replace `1` with the real number after create. These are examples only — none of them were run.

```bash
# Create and inspect
gh project create --owner mhaiprojects --title "Sahir"
gh project list --owner mhaiprojects
gh project view 1 --owner mhaiprojects
gh project view 1 --owner mhaiprojects --web
gh project field-list 1 --owner mhaiprojects

# Link to the repo
gh project link 1 --owner mhaiprojects --repo Sahir-AI

# Custom fields (Iteration must be created in the UI)
gh project field-create 1 --owner mhaiprojects \
  --name "Priority" --data-type SINGLE_SELECT --single-select-options "High,Medium,Low"
gh project field-create 1 --owner mhaiprojects \
  --name "Size" --data-type SINGLE_SELECT --single-select-options "XS,S,M,L,XL"
gh project field-create 1 --owner mhaiprojects \
  --name "Estimate" --data-type NUMBER
gh project field-create 1 --owner mhaiprojects \
  --name "ClickUp ID" --data-type TEXT

# Drafts vs real issues
gh project item-create 1 --owner mhaiprojects \
  --title "Model selector UI" --body "Pick, load, and eject local Ollama models; include a benchmark harness."

gh issue create --repo mhaiprojects/Sahir-AI \
  --title "Document microphone and audio devices" \
  --body "Document the supported input devices and the HTTPS secure-context requirement." \
  --project "Sahir"

gh project item-add 1 --owner mhaiprojects \
  --url https://github.com/mhaiprojects/Sahir-AI/issues/1

# Edit a field (Status, Priority, and so on) by issue URL
gh project item-edit 1 --owner mhaiprojects \
  --url https://github.com/mhaiprojects/Sahir-AI/issues/1 \
  --field "Status" --value "Todo"

gh project item-edit 1 --owner mhaiprojects \
  --url https://github.com/mhaiprojects/Sahir-AI/issues/1 \
  --field "Priority" --value "High"

# List
gh project item-list 1 --owner mhaiprojects --field "Status" --field "Priority"
gh project item-list 1 --owner mhaiprojects --query "assignee:@me -status:Done"

# Copy or close (rarely needed)
gh project copy 1 --source-owner mhaiprojects --target-owner mhaiprojects \
  --title "Sahir (copy)"
gh project close 1 --owner mhaiprojects
gh project close 1 --owner mhaiprojects --undo
```

Manual pages: [gh project](https://cli.github.com/manual/gh_project), [create](https://cli.github.com/manual/gh_project_create), [list](https://cli.github.com/manual/gh_project_list), [view](https://cli.github.com/manual/gh_project_view), [field-create](https://cli.github.com/manual/gh_project_field-create), [field-list](https://cli.github.com/manual/gh_project_field-list), [item-add](https://cli.github.com/manual/gh_project_item-add), [item-create](https://cli.github.com/manual/gh_project_item-create), [item-edit](https://cli.github.com/manual/gh_project_item-edit), [item-list](https://cli.github.com/manual/gh_project_item-list), [link](https://cli.github.com/manual/gh_project_link), [copy](https://cli.github.com/manual/gh_project_copy), [close](https://cli.github.com/manual/gh_project_close), [gh issue create](https://cli.github.com/manual/gh_issue_create), [gh auth login](https://cli.github.com/manual/gh_auth_login), [gh auth refresh](https://cli.github.com/manual/gh_auth_refresh).

GraphQL is always available via `gh api graphql` once the `project` scope is present. You cannot add an item and set a field in the same mutation; add first, then edit.

## Suggested starter setup (as planned before setup)

A compact setup that fits a Free user account and the current ClickUp workflow.

### Fields

- Built-in: Status (Todo, In Progress, Done), Assignees, Labels, Milestone, Type.
- Add: Priority (High / Medium / Low), Size (XS–XL), Estimate (number), Iteration (create in the UI), optional ClickUp ID (text), optional Start date and Target date if a roadmap is wanted.

### Views

1. **Board — Status** — default kanban, all open work. Optional field sum of Estimate. Filter: `-status:Done` if the Done column gets noisy before auto-archive kicks in.
2. **Table — Backlog** — group by Priority, sort by Priority then Estimate, show Type, Status, Assignees, Size, Estimate, Iteration, ClickUp ID.
3. **Board — This iteration** — same as the Status board, filter `iteration:@current`.
4. **Roadmap** — once dates or Iteration are populated; turn on milestone and iteration markers.

Slice-by Priority or Type is useful on the backlog table. Save each view after setting it up.

### Workflows

- Leave the default **closed → Done** and **PR merged → Done** workflows on.
- Turn on **Item added to project → Status: Todo**.
- Turn on the single Free-plan **Auto-add** workflow for `mhaiprojects/Sahir-AI` with `is:issue,pr`. Manually add anything already open (likely nothing today).
- Turn on **Auto-archive** with `is:closed,merged updated:<@today-1m`.
- Skip Actions and a PAT until a built-in workflow is not enough.

### Initial items

The open work now lives as issues on the [Sahir project board](https://github.com/users/mhaiprojects/projects/1); see also the [issue list](https://github.com/mhaiprojects/Sahir-AI/issues).

## Sources

GitHub Docs (fetched 16 November 2026):

- [About Projects](https://docs.github.com/en/issues/planning-and-tracking-with-projects/learning-about-projects/about-projects)
- [Quickstart for Projects](https://docs.github.com/en/issues/planning-and-tracking-with-projects/learning-about-projects/quickstart-for-projects)
- [Best practices for Projects](https://docs.github.com/en/issues/planning-and-tracking-with-projects/learning-about-projects/best-practices-for-projects)
- [Adding your project to a repository](https://docs.github.com/en/issues/planning-and-tracking-with-projects/managing-your-project/adding-your-project-to-a-repository)
- [Managing visibility of your projects](https://docs.github.com/en/issues/planning-and-tracking-with-projects/managing-your-project/managing-visibility-of-your-projects)
- [Managing access to your projects](https://docs.github.com/en/issues/planning-and-tracking-with-projects/managing-your-project/managing-access-to-your-projects)
- [Adding items to your project](https://docs.github.com/en/issues/planning-and-tracking-with-projects/managing-items-in-your-project/adding-items-to-your-project)
- [Converting draft issues to issues](https://docs.github.com/en/issues/planning-and-tracking-with-projects/managing-items-in-your-project/converting-draft-issues-to-issues)
- [Adding sub-issues](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/adding-sub-issues)
- [About parent issue and sub-issue progress fields](https://docs.github.com/en/issues/planning-and-tracking-with-projects/understanding-fields/about-parent-issue-and-sub-issue-progress-fields)
- [Managing issue types](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/managing-issue-types)
- [Changing the layout of a view](https://docs.github.com/en/issues/planning-and-tracking-with-projects/customizing-views-in-your-project/changing-the-layout-of-a-view)
- [Customizing the table layout](https://docs.github.com/en/issues/planning-and-tracking-with-projects/customizing-views-in-your-project/customizing-the-table-layout)
- [Filtering projects](https://docs.github.com/en/issues/planning-and-tracking-with-projects/customizing-views-in-your-project/filtering-projects)
- [Managing your views](https://docs.github.com/en/issues/planning-and-tracking-with-projects/customizing-views-in-your-project/managing-your-views)
- [About single select fields](https://docs.github.com/en/issues/planning-and-tracking-with-projects/understanding-fields/about-single-select-fields)
- [About text and number fields](https://docs.github.com/en/issues/planning-and-tracking-with-projects/understanding-fields/about-text-and-number-fields)
- [About date fields](https://docs.github.com/en/issues/planning-and-tracking-with-projects/understanding-fields/about-date-fields)
- [About iteration fields](https://docs.github.com/en/issues/planning-and-tracking-with-projects/understanding-fields/about-iteration-fields)
- [Using the built-in automations](https://docs.github.com/en/issues/planning-and-tracking-with-projects/automating-your-project/using-the-built-in-automations)
- [Adding items automatically](https://docs.github.com/en/issues/planning-and-tracking-with-projects/automating-your-project/adding-items-automatically)
- [Archiving items automatically](https://docs.github.com/en/issues/planning-and-tracking-with-projects/automating-your-project/archiving-items-automatically)
- [Automating Projects using Actions](https://docs.github.com/en/issues/planning-and-tracking-with-projects/automating-your-project/automating-projects-using-actions)
- [Using the API to manage Projects](https://docs.github.com/en/issues/planning-and-tracking-with-projects/automating-your-project/using-the-api-to-manage-projects)

GitHub CLI manuals: [gh project](https://cli.github.com/manual/gh_project) and the subcommands listed in section 7; [gh issue create](https://cli.github.com/manual/gh_issue_create); [gh auth login](https://cli.github.com/manual/gh_auth_login); [gh auth refresh](https://cli.github.com/manual/gh_auth_refresh).

ClickUp and sync: [GitHub integration](https://help.clickup.com/hc/en-us/articles/6305771568791-GitHub-integration); [Use GitHub Automations](https://help.clickup.com/hc/en-us/articles/6305836061463-Use-GitHub-Automations); [actions/add-to-project](https://github.com/actions/add-to-project/); [Unito GitHub limitations](https://guide.unito.io/limitations-github-integration) (GitHub Projects not supported as a sync source); [Zapier ClickUp–GitHub](https://zapier.com/apps/clickup/integrations/github); [Make ClickUp–GitHub](https://www.make.com/integrations/github/clickup).

Related docs in this repo: [feature checklist](./voice-assistant-feature-checklist.md), [GPU acceleration research](./gpu-acceleration-research.md), [public release prep](../plans/voice-assistant-github-prep.md).
