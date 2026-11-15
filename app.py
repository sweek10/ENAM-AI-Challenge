"""FINN web app. Run: uvicorn app:app --reload"""
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from finn import digest as D
from finn import settings as S
from finn.classify import CATEGORIES
from finn.portfolio import BY_SYMBOL

app = FastAPI(title="FINN")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def ago(dt_iso: str, ref: datetime) -> str:
    try:
        mins = int((ref - datetime.fromisoformat(dt_iso)).total_seconds() // 60)
    except ValueError:
        return ""
    if mins < 1:
        return "just now"
    if mins < 60:
        return f"{mins}m ago"
    if mins < 24 * 60:
        return f"{mins // 60}h ago"
    return f"{mins // 1440}d ago"


def spark(values: list[float], w: int = 64, h: int = 18) -> str:
    if len(values) < 2:
        return ""
    lo, hi = min(values), max(values)
    rng = (hi - lo) or 1
    pts = " ".join(f"{i * w / (len(values) - 1):.1f},{h - 2 - (v - lo) / rng * (h - 4):.1f}"
                   for i, v in enumerate(values))
    last_x, last_y = pts.split()[-1].split(",")
    return (f'<svg class="spark" viewBox="0 0 {w} {h}" width="{w}" height="{h}" aria-hidden="true">'
            f'<polyline points="{pts}"/><circle cx="{last_x}" cy="{last_y}" r="2"/></svg>')


def pct(x: float, dp: int = 1) -> str:
    if abs(x) < 0.5 * 10 ** -dp:
        return f"{0:.{dp}f}%"
    return f"{x:+.{dp}f}%".replace("-", "\u2212")


def tone(x: float) -> str:
    return "flat" if abs(x) < 0.05 else ("up" if x > 0 else "down")


templates.env.globals.update(pct=pct, tone=tone, ago=ago, spark=spark, BY_SYMBOL=BY_SYMBOL, LABELS=D.LABELS)


@app.get("/", response_class=HTMLResponse)
def home(request: Request, window: str = "24h"):
    window = window if window in D.WINDOWS else "24h"
    st = S.load()
    dg = D.build(window, st)
    return templates.TemplateResponse(request, "digest.html", {"d": dg, "s": st, "windows": list(D.WINDOWS)})


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, saved: int = 0):
    return templates.TemplateResponse(request, "settings.html", {
        "s": S.load(), "categories": [c for c in CATEGORIES if c != "unclassified"], "saved": saved})


@app.post("/settings")
async def settings_save(request: Request, price_move_pct: float = Form(...),
                        volume_multiple: float = Form(...), volume_lookback: int = Form(...)):
    form = await request.form()
    watch = [c for c in CATEGORIES if form.get(f"cat_{c}") == "watch"]
    mute = [c for c in CATEGORIES if form.get(f"cat_{c}") == "mute"]
    S.save(S.Settings(price_move_pct, volume_multiple, volume_lookback, watch, mute))
    return RedirectResponse("/settings?saved=1", status_code=303)


@app.get("/api/digest")
def api_digest(window: str = "24h"):
    dg = D.build(window, S.load())
    row = lambda i: {"symbol": i.a.symbol, "dt": i.a.dt, "title": i.title, "category": i.c.category,
                     "score": i.c.score, "reason": i.c.reason, "method": i.c.method, "pdf": i.a.pdf_url,
                     "related": len(i.related)}
    return JSONResponse({
        "window": dg.window, "source": dg.source, "total": dg.total, "stats": dg.stats,
        "need_you": [row(i) for i in dg.need_you], "notable": [row(i) for i in dg.notable],
        "routine": {k: [row(i) for i in v] for k, v in dg.routine.items()}, "hidden": len(dg.hidden),
        "alerts": [{"symbol": a.move.symbol, "change_pct": round(a.move.change_pct, 2),
                    "vol_multiple": a.move.vol_multiple and round(a.move.vol_multiple, 2), "kinds": a.kinds}
                   for a in dg.alerts],
    })


@app.get("/healthz")
def healthz():
    return {"ok": True}
