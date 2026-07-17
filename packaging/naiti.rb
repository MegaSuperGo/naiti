# Homebrew formula for naiti.
#
# This file belongs in a SEPARATE repository named `homebrew-tap`:
#
#   github.com/<you>/homebrew-tap/Formula/naiti.rb
#
# Then anyone can install with:
#
#   brew tap <you>/tap
#   brew install naiti
#
# See packaging/HOMEBREW.md for the full walkthrough, including how to
# generate the `resource` blocks automatically rather than by hand.

class Naiti < Formula
  include Language::Python::Virtualenv

  desc "Answer-quality benchmark for the Nexus AI knowledge base"
  homepage "https://github.com/felixtyx/naiti"
  url "https://github.com/felixtyx/naiti/archive/refs/tags/v1.0.0.tar.gz"
  sha256 "REPLACE_ME_WITH_THE_TARBALL_SHA256"
  license "MIT"

  depends_on "python@3.12"

  # ── Dependencies ────────────────────────────────────────────
  # Do NOT write these by hand. Generate them with:
  #
  #   brew install homebrew/cask/…   # (nothing needed; util is built in)
  #   brew update-python-resources naiti
  #
  # which resolves httpx, rich, groq, python-docx, python-pptx, openpyxl and
  # every transitive dependency, and writes correct `resource` blocks in place
  # of this comment. Re-run it whenever you bump a dependency.
  #
  # Example of the shape it produces:
  #
  # resource "httpx" do
  #   url "https://files.pythonhosted.org/packages/.../httpx-0.28.1.tar.gz"
  #   sha256 "..."
  # end

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match "naiti 1.0.0", shell_output("#{bin}/naiti --version")
    # naiti exits 1 with a helpful message when no project is in sight,
    # which is the correct behaviour outside a nexus_what checkout.
    assert_match "Could not find the nexus_what project",
                 shell_output("#{bin}/naiti --doctor 2>&1", 1)
  end
end
