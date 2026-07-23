"""Build the neofetch-style profile card SVGs (card-dark.svg, card-light.svg).

Layout, colours and the dot-leader idea are adapted from Andrew6rant/Andrew6rant.
Unlike that project the SVG is generated rather than patched in place, so the dot
leaders are recalculated from the content every run and never drift.

Run it with no environment set and it uses the unauthenticated REST API, which
covers repos, stars, followers and the account age. Set GITHUB_TOKEN as well and
it adds commit counts and contribution totals over the GraphQL API.
"""

import datetime
import json
import os
import urllib.error
import urllib.request

USER = os.environ.get("USER_NAME", "0xharkirat")
TOKEN = os.environ.get("GITHUB_TOKEN") or os.environ.get("ACCESS_TOKEN")

# Widest advance width any common 16px monospace fallback uses (Menlo, Courier New
# and Liberation Mono are all 9.6). Sizing to the worst case means the card never
# overflows on a machine without Consolas.
CHAR_W = 9.7
LINE_H = 20
BASELINE = 30
MARGIN = 15
GUTTER = 30  # space between the art and the panel
PANEL_COLS = 61

# Animated dither frames, produced by make_portrait.py. Regenerate that when the photo
# changes; it needs Pillow, which is why it is not part of this build.
with open("portrait-frames.json") as _f:
    PORTRAIT = json.load(_f)

# the panel and the card size themselves around whatever the portrait needs
ART_X = MARGIN
PANEL_X = round(ART_X + PORTRAIT["width"] + GUTTER)
WIDTH = round(PANEL_X + PANEL_COLS * CHAR_W + MARGIN)

THEMES = {
    "dark": {"bg": "#161b22", "fg": "#c9d1d9", "key": "#ffa657", "value": "#a5d6ff", "cc": "#616e7f"},
    "light": {"bg": "#f6f8fa", "fg": "#24292f", "key": "#953800", "value": "#0a3069", "cc": "#c2cfde"},
}


# --- row building -----------------------------------------------------------
# A fragment is (text, css_class, fill_char). A fill fragment stretches to push
# everything after it to the right edge of the panel.

def txt(text, cls=None):
    return (text, cls, None)


def fill(char, cls=None):
    return ("", cls, char)


def kv(key, value):
    """`. Key: ..... value` with the value flush right."""
    return [txt(". ", "cc"), txt(key, "key"), txt(":"), fill(".", "cc"), txt(value, "value")]


def kv_dotted(prefix, key, value):
    """`. Prefix.Key: ..... value`, both halves of the name coloured as a key."""
    return [
        txt(". ", "cc"), txt(prefix, "key"), txt("."), txt(key, "key"), txt(":"),
        fill(".", "cc"), txt(value, "value"),
    ]


def rule(title):
    return [txt(title + " "), fill("-", "cc")]


def gap():
    """A truly blank separator row.

    The reference card puts a faint ". " on these lines; on this card it read as a
    stray bullet floating above each section rule, so the row is left empty. text_block
    still emits a spacer tspan so the line keeps its height.
    """
    return []


def expand(frags, cols):
    """Resolve fill fragments so the row is exactly `cols` characters wide."""
    fixed = sum(len(t) for t, _, f in frags if f is None)
    slots = [i for i, (_, _, f) in enumerate(frags) if f is not None]
    if not slots:
        return [(t, c) for t, c, _ in frags]

    pad = max(0, cols - fixed)
    share, extra = divmod(pad, len(slots))
    out = []
    for i, (t, cls, f) in enumerate(frags):
        if f is None:
            out.append((t, cls))
            continue
        n = share + (extra if i == slots[-1] else 0)
        # dot leaders keep a space either side so they never touch the text
        out.append((" " * n if f == "." and n < 3 else
                    " " + "." * (n - 2) + " " if f == "." else f * n, cls))
    return out


# --- stats ------------------------------------------------------------------

def get_json(url, headers=None):
    # The token has to go on the REST calls too, not just GraphQL. Without it these run
    # against the 60/hour anonymous limit and the whole build fails on a busy day.
    auth = {"authorization": "token " + TOKEN} if TOKEN else {}
    req = urllib.request.Request(url, headers={
        "accept": "application/vnd.github+json", **auth, **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def graphql(query, variables):
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(
        "https://api.github.com/graphql", data=body,
        headers={"authorization": "token " + TOKEN, "content-type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.load(resp)
    if "errors" in payload:
        raise RuntimeError(payload["errors"])
    return payload["data"]


def rest_stats():
    user = get_json(f"https://api.github.com/users/{USER}")
    stars = 0
    page = 1
    while True:
        repos = get_json(f"https://api.github.com/users/{USER}/repos?per_page=100&page={page}")
        if not repos:
            break
        stars += sum(r["stargazers_count"] for r in repos if not r["fork"])
        if len(repos) < 100:
            break
        page += 1
    created = datetime.datetime.strptime(user["created_at"], "%Y-%m-%dT%H:%M:%SZ").date()
    return {
        "repos": user["public_repos"],
        "followers": user["followers"],
        "stars": stars,
        "created": created,
    }


def graphql_stats(created):
    """Commit totals per year plus the contributed-repo and past-year counts."""
    year_query = """
    query($login: String!, $from: DateTime!, $to: DateTime!) {
      user(login: $login) {
        contributionsCollection(from: $from, to: $to) {
          totalCommitContributions
          restrictedContributionsCount
        }
      }
    }"""
    commits = 0
    for year in range(created.year, datetime.date.today().year + 1):
        data = graphql(year_query, {
            "login": USER,
            "from": f"{year}-01-01T00:00:00Z",
            "to": f"{year}-12-31T23:59:59Z",
        })
        cc = data["user"]["contributionsCollection"]
        commits += cc["totalCommitContributions"] + cc["restrictedContributionsCount"]

    data = graphql("""
    query($login: String!) {
      user(login: $login) {
        repositoriesContributedTo(contributionTypes: [COMMIT, PULL_REQUEST], includeUserRepositories: false) {
          totalCount
        }
        contributionsCollection { contributionCalendar { totalContributions } }
      }
    }""", {"login": USER})
    return {
        "commits": commits,
        "contributed": data["user"]["repositoriesContributedTo"]["totalCount"],
        "past_year": data["user"]["contributionsCollection"]["contributionCalendar"]["totalContributions"],
    }


class Unavailable(Exception):
    """The API could not be reached, so any card built now would be worse than the one
    already committed."""


def collect():
    stats = {"repos": "-", "followers": "-", "stars": "-",
             "commits": "-", "contributed": "-", "past_year": "-"}
    created = datetime.date.today()
    try:
        rest = rest_stats()
        created = rest.pop("created")
        stats.update(rest)
    except (urllib.error.URLError, KeyError, ValueError) as err:
        # Fatal on purpose. Writing "-" into every row would blank a working card on the
        # next commit; leaving yesterday's card in place is the better failure.
        raise Unavailable(f"REST stats unavailable ({err})") from err
    if TOKEN:
        try:
            stats.update(graphql_stats(created))
        except (urllib.error.URLError, RuntimeError, KeyError) as err:
            print(f"warning: GraphQL stats unavailable ({err})")
    else:
        print("warning: no GITHUB_TOKEN, commit and contribution counts left blank")
    return {k: f"{v:,}" if isinstance(v, int) else v for k, v in stats.items()}


# --- panel ------------------------------------------------------------------

def panel(s):
    return [
        [txt("Hark", "key"), txt("@"), txt("singh", "value"), txt(" "), fill("-", "cc")],
        kv("Kernel", "Software Engineer, Flutter + .NET"),
        kv("Shipping", "SSW EagleEye (ssweagleeye.com)"),
        # No emoji anywhere in the panel: they render about two columns wide but count as
        # one character, which would silently break the flush-right dot leaders.
        kv("Relationship", "single, open to a life-long commit"),
        gap(),
        kv_dotted("Languages", "Programming", "Dart, C#, TypeScript"),
        kv_dotted("Languages", "Real", "English, Punjabi, Hindi"),
        gap(),
        kv_dotted("Hobbies", "Software", "Flutter projects, open source"),
        kv_dotted("Hobbies", "Real", "Tabla, vlogging"),
        gap(),
        # These are display only. An SVG loaded through <img>, which is how GitHub
        # serves README images, cannot activate links - the clickable copies live in
        # the README markdown underneath the card.
        rule("- Contact"),
        kv("Website", "harksingh.com"),
        kv("LinkedIn", "linkedin.com/in/talesofhark"),
        kv("X", "x.com/talesofhark"),
        kv("YouTube", "youtube.com/@talesofhark"),
        kv("Instagram", "instagram.com/talesofhark"),
        kv("SSW", "ssw.com.au/people/hark-singh"),
        gap(),
        rule("- GitHub Stats"),
        [
            txt(". ", "cc"), txt("Repos", "key"), txt(":"), fill(".", "cc"), txt(s["repos"], "value"),
            txt(" {"), txt("Contributed", "key"), txt(": "), txt(s["contributed"], "value"), txt("}"),
            txt(" | "), txt("Stars", "key"), txt(":"), fill(".", "cc"), txt(s["stars"], "value"),
        ],
        [
            txt(". ", "cc"), txt("Commits", "key"), txt(":"), fill(".", "cc"), txt(s["commits"], "value"),
            txt(" | "), txt("Followers", "key"), txt(":"), fill(".", "cc"), txt(s["followers"], "value"),
        ],
        kv("Contributions (past year)", s["past_year"]),
    ]


# --- render -----------------------------------------------------------------

def esc(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def text_block(x, rows):
    out = [f'<text x="{x}" y="{BASELINE}" fill="{{fg}}">']
    for i, frags in enumerate(rows):
        y = BASELINE + i * LINE_H
        first = True
        line = []
        for content, cls in frags:
            if not content:
                continue
            attrs = f' x="{x}" y="{y}"' if first else ""
            cls_attr = f' class="{cls}"' if cls else ""
            line.append(f"<tspan{attrs}{cls_attr}>{esc(content)}</tspan>")
            first = False
        if first:  # nothing on this row, still needs to occupy the line
            line.append(f'<tspan x="{x}" y="{y}"> </tspan>')
        out.append("".join(line))
    out.append("</text>")
    return "\n".join(out)


def portrait_block(theme_name, height):
    """Stack the dither frames and cycle them with CSS.

    Each frame is visible for one slot of the loop; negative animation-delay staggers
    them so exactly one shows at a time. steps(1, end) keeps the cut hard - crossfading
    would blur the dots into grey and lose the whole point of a binary dither.
    """
    frames = PORTRAIT["frames"][theme_name]
    n, dur = len(frames), PORTRAIT["duration"]
    w, h = PORTRAIT["width"], PORTRAIT["height"]
    y = round((height - h) / 2)

    css = [f".fr{{opacity:0;animation:flick {dur}s steps(1,end) infinite;"
           "image-rendering:pixelated}",
           f"@keyframes flick{{0%{{opacity:1}}{100 / n:.4f}%{{opacity:0}}}}"]
    layers = []
    for i, data in enumerate(frames):
        css.append(f"#fr{i}{{animation-delay:{-dur * i / n:.4f}s}}")
        layers.append(f'<image id="fr{i}" class="fr" x="{ART_X}" y="{y}" width="{w}"'
                      f' height="{h}" href="data:image/png;base64,{data}"/>')
    return "\n".join(css), "\n".join(layers)


def build(theme_name, rows, height):
    t = THEMES[theme_name]
    art_css, art_layers = portrait_block(theme_name, height)
    body = "\n".join([
        "<?xml version='1.0' encoding='UTF-8'?>",
        f'<svg xmlns="http://www.w3.org/2000/svg" font-family="ConsolasFallback,Consolas,monospace"'
        f' width="{WIDTH}px" height="{height}px" font-size="16px">',
        "<style>",
        "@font-face {src: local('Consolas'); font-family: 'ConsolasFallback'; font-display: swap; size-adjust: 109%;}",
        f".key {{fill: {t['key']};}}",
        f".value {{fill: {t['value']};}}",
        f".cc {{fill: {t['cc']};}}",
        art_css,
        "text, tspan {white-space: pre;}",
        "</style>",
        f'<rect width="{WIDTH}px" height="{height}px" fill="{t["bg"]}" rx="15"/>',
        art_layers,
        text_block(PANEL_X, rows),
        "</svg>",
    ])
    return body.replace("{fg}", t["fg"]) + "\n"


def selfcheck():
    assert expand([txt("a"), fill("."), txt("b")], 10) == [("a", None), (" ...... ", None), ("b", None)]
    assert expand([txt("a"), fill("."), txt("b")], 4) == [("a", None), ("  ", None), ("b", None)]
    assert expand([txt("a"), fill("-")], 5) == [("a", None), ("----", None)]


def main():
    selfcheck()
    try:
        stats = collect()
    except Unavailable as err:
        print(f"warning: {err}; card left unchanged")
        return
    rows = [expand(r, PANEL_COLS) for r in panel(stats)]
    height = BASELINE + len(rows) * LINE_H

    assert PANEL_X + PANEL_COLS * CHAR_W <= WIDTH, "panel overflows the card"
    assert ART_X + PORTRAIT["width"] < PANEL_X, "portrait collides with the panel"
    assert PORTRAIT["height"] <= height, "portrait is taller than the card"
    assert len(set(len(f) for f in PORTRAIT["frames"].values())) == 1, "themes disagree on frame count"
    assert all(len(r) == PANEL_COLS for r in (
        "".join(t for t, _ in row) for row in rows if len(row) > 1)), "panel rows are not flush"

    for name in THEMES:
        path = f"card-{name}.svg"
        with open(path, "w") as f:
            f.write(build(name, rows, height))
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
