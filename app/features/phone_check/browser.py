"""The part of the phone check that opens the game in a headless browser (Playwright's Chromium) at phone widths and
measures its start screen. Used only when PHONE_CHECK_BROWSER is on and Playwright is installed (see the Dockerfile)."""
import logging

log = logging.getLogger(__name__)

WIDTHS = (320, 375)  # a small iPhone and a common Android width
OFF_SCREEN = 10  # this many visible parts outside the screen: worth a look (a few are often slide-out menus)
SETTLE_MS = 2500  # after the page loads, time for the game's code to lay it out
# What the page looks like at this width: how wide it really is, and how many visible parts sit past the right edge.
MEASURE = """() => {
  const vw = window.innerWidth;
  const off = [...document.querySelectorAll('body *')].filter((el) => {
    const b = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return b.width > 0 && b.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && b.right > vw + 2;
  }).length;
  return { vw, sw: document.documentElement.scrollWidth, off };
}"""


def available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return False
    return True


def findings_from(width: int, m: dict) -> list[dict]:
    """What one measurement at `width` px means for the game's owner."""
    found = []
    # A phone zooms a too-wide page out to fit (innerWidth grows), unless the viewport tag forbids zooming (then the page
    # scrolls sideways instead), so compare with the phone's own width, not innerWidth.
    page_width = max(m["sw"], m["vw"])
    if page_width > width + 2:
        found.append({"code": "scrolls_sideways", "message": f"At {width} px wide the page is {page_width} px wide, so "
                      "phones zoom it out until everything is tiny, or players have to scroll sideways."})
    elif m["off"] >= OFF_SCREEN:
        found.append({"code": "off_screen", "message": f"At {width} px wide, {m['off']} parts of the start screen are "
                      "past the right edge of the screen, so players can't see or tap them."})
    return found


def check(url: str, timeout_ms: int = 20_000) -> list[dict]:
    """Open the game at each phone width. Raises if the browser can't be started or the page doesn't load."""
    from playwright.sync_api import sync_playwright

    found: list[dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            for width in WIDTHS:
                context = browser.new_context(viewport={"width": width, "height": 700}, device_scale_factor=2,
                                              is_mobile=True, has_touch=True, user_agent="start.ng phone check")
                try:
                    page = context.new_page()
                    page.goto(url, wait_until="load", timeout=timeout_ms)
                    page.wait_for_timeout(SETTLE_MS)
                    found += findings_from(width, page.evaluate(MEASURE))
                finally:
                    context.close()
                if found:
                    break  # the narrowest width that has a problem is enough to tell the owner
        finally:
            browser.close()
    return found
