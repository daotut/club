#!/usr/bin/env python3
"""
web-trace — записать, что страница делает с сетью: при загрузке и после клика.

    python3 web-trace.py <URL> <папка> [--click SEL ...] [--wait 6] [--mobile]
                         [--proxy socks5://127.0.0.1:1080] [--stay]

  --click SEL   селектор Playwright, можно несколько раз, кликаются по порядку:
                "text=Telegram", "a[href*='t.me']", "#btn". Каждый клик — своя фаза.
  --wait N      сколько секунд ждать после загрузки и после каждого клика (по умолчанию 6).
  --mobile      профиль iPhone (лендинги часто ведут себя иначе на телефоне).
  --stay        не давать клику увести страницу: переход перехватывается, адрес
                записывается, страница остаётся (нужно, чтобы снять deeplink до ухода).

Пишет в <папка>:
  requests.jsonl   каждый запрос: фаза, метод, адрес, тип, статус, тело POST (до 4 КБ)
  trace.har        полный HAR (открывается в DevTools → Network → Import)
  scripts/         тела всех JS-ответов — искать по ним `rg -uu`
  page.html        DOM после загрузки
  links.json       все ссылки страницы + ссылки в мессенджеры отдельно
  storage.json     куки и localStorage до и после кликов
  navigations.json куда страница пыталась уйти (переходы, всплывающие окна, tg://)
  console.log      консоль страницы
  shot-*.png       снимки по фазам
В stdout — короткая сводка: чужие хосты по фазам и все запросы с телом после клика.

Ничего не отправляет сам, кроме того, что отправит открытая страница.
Часть скилла daoreverse (DAO CLUB).
"""
import argparse, json, os, re, sys, time
from collections import Counter, defaultdict
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

MESSENGER = re.compile(r"(t\.me/|telegram\.me/|tg://|max\.ru/|wa\.me/|whatsapp|vk\.me/|viber)", re.I)


def host(u):
    try:
        return urlsplit(u).hostname or ""
    except Exception:
        return ""


def site_root(h):
    parts = h.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else h


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("out")
    ap.add_argument("--click", action="append", default=[])
    ap.add_argument("--wait", type=float, default=6)
    ap.add_argument("--mobile", action="store_true")
    ap.add_argument("--proxy")
    ap.add_argument("--stay", action="store_true")
    a = ap.parse_args()

    os.makedirs(os.path.join(a.out, "scripts"), exist_ok=True)
    own = site_root(host(a.url))
    phase = {"v": "load"}
    reqs, navs, console = [], [], []
    seen_scripts = set()

    with sync_playwright() as p:
        launch = {"proxy": {"server": a.proxy}} if a.proxy else {}
        browser = p.chromium.launch(**launch)
        ctx_args = dict(p.devices["iPhone 13"]) if a.mobile else {"viewport": {"width": 1366, "height": 900}}
        ctx_args.update(record_har_path=os.path.join(a.out, "trace.har"), ignore_https_errors=True, locale="ru-RU")
        ctx = browser.new_context(**ctx_args)

        def on_request(r):
            body = None
            try:
                body = r.post_data
            except Exception:
                pass
            reqs.append({
                "phase": phase["v"], "t": round(time.time(), 3), "method": r.method, "url": r.url,
                "type": r.resource_type, "host": host(r.url),
                "third": site_root(host(r.url)) != own,
                "post": (body[:4096] if body else None), "status": None,
                "frame": (r.frame.url[:200] if r.frame else None),
            })

        def on_response(resp):
            for q in reversed(reqs):
                if q["url"] == resp.url and q["status"] is None:
                    q["status"] = resp.status
                    loc = resp.headers.get("location")
                    if loc:
                        q["location"] = loc
                    break
            if resp.request.resource_type == "script" and resp.url not in seen_scripts:
                seen_scripts.add(resp.url)
                try:
                    name = re.sub(r"[^A-Za-z0-9._-]+", "_", resp.url.split("://", 1)[-1])[:150]
                    open(os.path.join(a.out, "scripts", name + ".js"), "wb").write(resp.body())
                except Exception:
                    pass

        ctx.on("request", on_request)
        ctx.on("response", on_response)
        ctx.on("page", lambda pg: navs.append({"phase": phase["v"], "kind": "popup", "url": pg.url}))

        page = ctx.new_page()
        page.on("console", lambda m: console.append(f"[{phase['v']}] {m.type}: {m.text}"))

        # WebSocket в HAR не попадает — пишем открытие и кадры сами (обрезка 2 КБ)
        def on_ws(ws):
            reqs.append({"phase": phase["v"], "t": round(time.time(), 3), "method": "WS", "url": ws.url,
                         "type": "websocket", "host": host(ws.url), "third": site_root(host(ws.url)) != own,
                         "post": None, "status": "open", "frame": None})
            ws.on("framesent", lambda d: navs.append({"phase": phase["v"], "kind": "ws-sent", "url": f"{ws.url[:80]} {str(d)[:2048]}"}))
            ws.on("framereceived", lambda d: navs.append({"phase": phase["v"], "kind": "ws-received", "url": f"{ws.url[:80]} {str(d)[:2048]}"}))
            ws.on("close", lambda _: navs.append({"phase": phase["v"], "kind": "ws-closed", "url": ws.url[:200]}))
        page.on("websocket", on_ws)
        page.on("framenavigated", lambda fr: fr == page.main_frame and navs.append(
            {"phase": phase["v"], "kind": "navigate", "url": fr.url}))

        if a.stay:
            def guard(route):
                req = route.request
                if req.is_navigation_request() and req.frame == page.main_frame and phase["v"] != "load":
                    navs.append({"phase": phase["v"], "kind": "blocked-navigation", "url": req.url})
                    route.abort()
                else:
                    route.continue_()
            page.route("**/*", guard)

        def snapshot_storage(tag):
            try:
                ls = page.evaluate("() => Object.fromEntries(Object.entries(localStorage))")
            except Exception:
                ls = {}
            return {"when": tag, "cookies": ctx.cookies(), "localStorage": ls}

        page.goto(a.url, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_load_state("networkidle", timeout=a.wait * 1000)
        except Exception:
            pass
        page.wait_for_timeout(a.wait * 1000)
        page.screenshot(path=os.path.join(a.out, "shot-load.png"), full_page=False)
        open(os.path.join(a.out, "page.html"), "w").write(page.content())

        links = page.evaluate("""() => [...document.querySelectorAll('a[href]')].map(a => ({
            text: (a.innerText || '').trim().slice(0, 80), href: a.href, target: a.target || ''}))""")
        storage = [snapshot_storage("load")]

        for i, sel in enumerate(a.click, 1):
            phase["v"] = f"click{i}"
            try:
                # снять href до клика: часть скриптов подменяет его прямо на mousedown
                before = page.eval_on_selector(sel, "e => e.href || e.getAttribute('onclick') || ''")
                navs.append({"phase": phase["v"], "kind": "href-before-click", "url": before})
                page.click(sel, timeout=10000, no_wait_after=True)
            except Exception as e:
                navs.append({"phase": phase["v"], "kind": "click-failed", "url": f"{sel}: {e}"[:300]})
            page.wait_for_timeout(a.wait * 1000)
            try:
                after = page.eval_on_selector(sel, "e => e.href || ''")
                navs.append({"phase": phase["v"], "kind": "href-after-click", "url": after})
            except Exception:
                pass
            try:
                page.screenshot(path=os.path.join(a.out, f"shot-click{i}.png"))
            except Exception:
                pass
            storage.append(snapshot_storage(phase["v"]))

        ctx.close()
        browser.close()

    with open(os.path.join(a.out, "requests.jsonl"), "w") as f:
        for q in reqs:
            f.write(json.dumps(q, ensure_ascii=False) + "\n")
    json.dump({"all": links, "messengers": [l for l in links if MESSENGER.search(l["href"])]},
              open(os.path.join(a.out, "links.json"), "w"), ensure_ascii=False, indent=1)
    json.dump(storage, open(os.path.join(a.out, "storage.json"), "w"), ensure_ascii=False, indent=1)
    json.dump(navs, open(os.path.join(a.out, "navigations.json"), "w"), ensure_ascii=False, indent=1)
    open(os.path.join(a.out, "console.log"), "w").write("\n".join(console))

    # --- сводка
    print(f"ЦЕЛЬ: {a.url}  (свой домен: {own})")
    print(f"запросов: {len(reqs)}, скриптов сохранено: {len(seen_scripts)}, папка: {a.out}")
    by_phase = defaultdict(Counter)
    for q in reqs:
        if q["third"]:
            by_phase[q["phase"]][q["host"]] += 1
    for ph, c in by_phase.items():
        print(f"\n--- чужие хосты, фаза {ph}")
        for h, n in c.most_common(25):
            print(f"  {n:4} {h}")
    msg = [l for l in links if MESSENGER.search(l["href"])]
    if msg:
        print("\n--- ссылки в мессенджеры на странице")
        for l in msg[:20]:
            print(f"  {l['text'][:40]!r:44} {l['href'][:160]}")
    if a.click:
        print("\n--- после кликов: запросы с телом или к чужим")
        for q in reqs:
            if q["phase"] != "load" and (q["post"] or q["third"]):
                print(f"  [{q['phase']}] {q['method']} {q['status']} {q['url'][:200]}")
                if q["post"]:
                    print(f"        тело: {q['post'][:400]}")
        print("\n--- переходы")
        for n in navs:
            if n["phase"] != "load":
                print(f"  [{n['phase']}] {n['kind']}: {str(n['url'])[:200]}")


if __name__ == "__main__":
    sys.exit(main())
