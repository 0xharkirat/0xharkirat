"""Refresh the generated sections of README.md.

Two blocks are rewritten in place; everything else in the README is hand-written and
never touched:

    <!-- contributions:start -->   merged PRs to other people's repos, newest first
    <!-- projects:start -->        own public repos, with release tag or WIP

Anything that fails - network, rate limit, missing markers - leaves that block exactly
as it was and exits 0. A stale list beats a workflow that blanks the section.

Deliberately stdlib only, same as build_card.py, so the workflow needs no pip step.
Unauthenticated it will usually run out of quota partway; set GITHUB_TOKEN.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request

USER = os.environ.get("USER_NAME", "0xharkirat")
TOKEN = os.environ.get("GITHUB_TOKEN") or os.environ.get("ACCESS_TOKEN")
README = "README.md"

# Owners whose repos are not "open source contributions": his own work belongs under
# Projects, and SSW and TinaCMS are day-job repos.
#
# openappcapabilityprotocol is his own org - its repos are forks he opened PR #1 against
# to add his own SDK, so they are projects, not contributions to someone else's code.
# Newest-first ordering let them take 7 of the 12 slots. Drop it from this set to
# include them again.
EXCLUDE_OWNERS = {"0xharkirat", "tinacms", "openappcapabilityprotocol"}

# Matched as prefixes, because SSW work is spread over SSWConsulting, SSWEmployment and
# SSW-FireBootCamp - an exact-match list quietly let the last two through.
EXCLUDE_PREFIXES = ("ssw",)


def excluded(owner):
    owner = owner.lower()
    return owner in EXCLUDE_OWNERS or owner.startswith(EXCLUDE_PREFIXES)

# Repos that are public but not worth showcasing - old coursework, one-off demos, and
# supporting infrastructure. Archiving them on GitHub drops them out automatically too;
# this list is for the ones worth keeping active but out of the README.
EXCLUDE_REPOS = {
    "sri-darbar-sahib-live", "gsoc-proposal", "autozoom-camera-flutter", "airline",
    "homebrew-tap", "dynamic_notch", "fumadocs-with-tinacms-v1",
    "vfs-passport-watcher", "impact_poc", "tictactoe",
    "demo-with-ui-components", "playwright-beginner", "just_think",
    "bhai-hira-singh-translation", "hark-tina-blog", "hark-blog",
    "linkedin-title-automation", "SSW.Email.Templates", "yakshaver_mobile_poc",
    "flutter_riverpod_clock", "its_urgent_poc_public", "harkiratsingh-cv",
    "ragi_duties.api",
}

# Shown even without a description. Blacklisting scratch repos one by one never
# converges - there are 115 of them and removing one just promotes the next - so the
# gate is inverted: a description is what makes a repo appear at all. These two are
# active enough to show now; delete them from here once they have descriptions.
ALWAYS_INCLUDE = {"open-obsbot-remote", "humation_flutter"}

CONTRIB_LIMIT = 12
PROJECT_LIMIT = 16   # no "and N more" tail, so this is the whole list that shows
MAX_PAGES = 6      # search and repo listing both page at 100


class Unavailable(Exception):
    """The API could not be reached, so the block keeps whatever it already has."""


def get_json(url):
    headers = {"accept": "application/vnd.github+json",
               "user-agent": f"{USER}-profile-readme"}
    if TOKEN:
        headers["authorization"] = "token " + TOKEN
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as err:
        if err.code == 404:
            return None                      # asked about something that does not exist
        raise Unavailable(f"{url} -> HTTP {err.code}") from err
    except (urllib.error.URLError, ValueError) as err:
        raise Unavailable(f"{url} -> {err}") from err


def paged(url_for):
    out = []
    for page in range(1, MAX_PAGES + 1):
        batch = get_json(url_for(page))
        items = batch.get("items", batch) if isinstance(batch, dict) else batch
        if not items:
            break
        out.extend(items)
        if len(items) < 100:
            break
    return out


# --- contributions ----------------------------------------------------------

def merged_at(pr):
    """Merge timestamp, ISO-8601 so plain string comparison sorts correctly."""
    return (pr.get("pull_request") or {}).get("merged_at") or pr.get("closed_at") or ""


def contributions():
    prs = paged(lambda p: "https://api.github.com/search/issues"
                          f"?q=type:pr+author:{USER}+is:merged&per_page=100&page={p}")
    # one entry per repo, the most recent - otherwise a repo contributed to 39 times
    # would fill the section on its own
    best = {}
    for pr in prs:
        match = re.search(r"/repos/([^/]+)/([^/]+)$", pr["repository_url"])
        if not match or excluded(match.group(1)):
            continue
        slug = f"{match.group(1)}/{match.group(2)}"
        if slug not in best or merged_at(pr) > merged_at(best[slug]):
            best[slug] = pr
    if not best:
        raise Unavailable("no external merged PRs found")

    entries = sorted(best.items(), key=lambda e: merged_at(e[1]), reverse=True)
    lines = [f"- [{slug}#{pr['number']}]({pr['html_url']}) - {pr['title'].strip().rstrip('.')}"
             for slug, pr in entries[:CONTRIB_LIMIT]]
    if len(entries) > CONTRIB_LIMIT:
        lines.append(f"- ...and {len(entries) - CONTRIB_LIMIT} more across other repositories")
    return "\n".join(lines), f"{len(entries)} repos from {len(prs)} merged PRs"


# --- projects ---------------------------------------------------------------

def projects():
    repos = paged(lambda p: f"https://api.github.com/users/{USER}/repos"
                            f"?per_page=100&page={p}&sort=pushed")
    # A repo with no description reads as a blank line in the list, and adding one is
    # the cheapest way for him to opt a repo in, so treat the description as the gate.
    def worth_showing(repo):
        """A description is the sign someone meant this repo to be seen. Tutorial and
        scratch repos do not have one, and there are ~90 of those."""
        return repo["name"] in ALWAYS_INCLUDE or (repo.get("description") or "").strip()

    keep = [r for r in repos
            if not r["fork"] and not r["archived"] and not r.get("private")
            and r["name"].lower() != USER.lower()
            and r["name"] not in EXCLUDE_REPOS
            and worth_showing(r)]
    if not keep:
        raise Unavailable("no public repos found")

    # Most recently pushed first, so whatever is being worked on right now leads and the
    # list stays honest about what is actually alive.
    keep.sort(key=lambda r: r["pushed_at"], reverse=True)

    lines, tagged = [], 0
    for repo in keep[:PROJECT_LIMIT]:
        release = get_json(f"https://api.github.com/repos/{repo['full_name']}/releases/latest")
        tag = (release or {}).get("tag_name", "").strip()
        # No GitHub Release does not mean unfinished - rough_notation ships on pub.dev and
        # anvaad-py on PyPI, and labelling those "WIP" would be a lie. The badge shows a
        # real release when there is one, and otherwise only what the `wip` topic claims.
        badge = tag or ("WIP" if "wip" in repo.get("topics", []) else "")
        tagged += bool(tag)
        line = f"- [{repo['name']}]({repo['html_url']})"
        if badge:
            line += f" `{badge}`"
        description = (repo.get("description") or "").strip().rstrip(".")
        if description:
            line += f" - {description}"
        # Several repos set homepage to their own releases page, which would render a
        # second link straight back to the repo already linked by the name.
        home = (repo.get("homepage") or "").strip()
        if home.startswith("http") and not home.rstrip("/").startswith(repo["html_url"]):
            line += f" - [site]({home})"
        lines.append(line)
    return "\n".join(lines), f"{len(keep)} candidates, {len(lines)} shown, {tagged} with a release"


# --- README splicing --------------------------------------------------------

def splice(body, name, block):
    """Replace one marked region. Returns None if its markers are missing or crossed."""
    start, end = f"<!-- {name}:start -->", f"<!-- {name}:end -->"
    a, b = body.find(start), body.find(end)
    if a == -1 or b == -1 or b < a:
        return None
    return body[:a + len(start)] + "\n" + block + "\n" + body[b:]


def selfcheck():
    doc = "x\n<!-- p:start -->\nold\n<!-- p:end -->\ny\n"
    assert splice(doc, "p", "new") == "x\n<!-- p:start -->\nnew\n<!-- p:end -->\ny\n"
    assert splice("nothing here", "p", "new") is None
    assert splice("<!-- p:end -->\n<!-- p:start -->", "p", "new") is None, "crossed markers"
    # a block must never swallow the neighbouring one
    two = ("<!-- a:start -->\n1\n<!-- a:end -->\n<!-- b:start -->\n2\n<!-- b:end -->\n")
    assert splice(two, "a", "N") == "<!-- a:start -->\nN\n<!-- a:end -->\n<!-- b:start -->\n2\n<!-- b:end -->\n"


def main():
    selfcheck()
    with open(README) as f:
        body = original = f.read()

    for name, build in (("contributions", contributions), ("projects", projects)):
        try:
            block, summary = build()
        except Unavailable as err:
            print(f"warning: {name} unavailable ({err}); block left unchanged")
            continue
        spliced = splice(body, name, block)
        if spliced is None:
            print(f"warning: <!-- {name}:start/end --> markers missing; block skipped")
            continue
        body = spliced
        print(f"{name}: {summary}")

    if body == original:
        print(f"{README} already up to date")
        return
    with open(README, "w") as f:
        f.write(body)
    print(f"wrote {README}")


if __name__ == "__main__":
    sys.exit(main())
