# Shipping naiti so coaches can install it with one command

Three options, cheapest first. You can do all three — they're not exclusive.

---

## Option A — pipx (works today, zero infrastructure)

Nothing to set up beyond pushing the code to GitHub.

```bash
pipx install git+https://github.com/felixtyx/naiti
naiti --doctor
```

Works on macOS, Linux and Windows. This is the standard way to ship a Python
CLI and the one to put at the top of your instructions. If a coach doesn't have
`pipx`: `brew install pipx` or `python3 -m pip install --user pipx`.

`uv` users get the same thing, faster:

```bash
uv tool install git+https://github.com/felixtyx/naiti
```

---

## Option B — your own Homebrew tap (what you asked about)

**Yes, this is possible, and it's the professional-looking one.** A "tap" is
just a GitHub repo Homebrew knows how to read. You do *not* need anyone's
approval.

### 1. Publish naiti and tag a release

```bash
cd naiti
git init && git add -A && git commit -m "naiti 1.0.0"
gh repo create felixtyx/naiti --public --source=. --push
git tag v1.0.0 && git push --tags
```

### 2. Get the tarball checksum

```bash
curl -sL https://github.com/felixtyx/naiti/archive/refs/tags/v1.0.0.tar.gz \
  | shasum -a 256
```

### 3. Create the tap repo

The name **must** start with `homebrew-`:

```bash
gh repo create felixtyx/homebrew-tap --public --clone
cd homebrew-tap && mkdir -p Formula
cp ../naiti/packaging/naiti.rb Formula/naiti.rb
```

Paste the sha256 from step 2 into `Formula/naiti.rb`.

### 4. Generate the dependency blocks

Homebrew builds Python apps in an isolated virtualenv and needs every
dependency pinned. Don't write them by hand:

```bash
brew tap felixtyx/tap
brew update-python-resources naiti
```

This rewrites the formula with correct `resource` blocks for `httpx`, `rich`,
`groq`, `python-docx`, `python-pptx`, `openpyxl` and everything underneath.

### 5. Test and push

```bash
brew install --build-from-source Formula/naiti.rb
brew test naiti
brew audit --strict naiti

git add -A && git commit -m "naiti 1.0.0" && git push
```

### 6. Coaches now run

```bash
brew tap felixtyx/tap
brew install naiti
```

To ship an update: bump `version` in `pyproject.toml`, tag a new release,
update `url` + `sha256` in the formula, push the tap. Users get it with
`brew upgrade naiti`.

---

## What about Homebrew core (plain `brew install naiti`)?

Not realistic, and worth knowing why before you spend time on it.
`homebrew-core` requires **notability**: roughly 75+ GitHub stars, 30+ forks or
30+ watchers, plus a stable release history, and it must be useful to a general
audience. A tool that only tests one private codebase would be rejected on
scope alone, regardless of stars.

The tap gives you the same one-command install. The only difference is that
users type `brew tap felixtyx/tap` once first. Every serious internal tool ships
this way.

---

## Option C — bundle it with the review copy

If you're sending `nexus_what` to the coaches anyway, you can drop `naiti/`
beside it and have them run:

```bash
cd naiti && pip install -e . && naiti --doctor
```

Simplest for a one-off review, but then naiti isn't a standalone tool and your
tester ships with your app.

---

## Recommended for the coaches

Put this in your handover README:

````markdown
## Testing the AI

Install the tester:

```bash
brew tap felixtyx/tap && brew install naiti
```

Point it at your own Groq API key (free at console.groq.com):

```bash
naiti -api gsk_your_key_here
```

Check everything is ready, then run:

```bash
naiti --doctor
naiti -ai -n 30 INTERNAL-TESTING-5312
```

The test company is created and removed automatically. A full run needs about
240k Groq tokens; the free tier allows 200k/day, so `-n 30` fits in one sitting.
````
