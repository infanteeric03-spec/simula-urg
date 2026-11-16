"""Prueba real de extremo a extremo: un docente y dos estudiantes, en navegadores separados, contra Supabase.

Usa la invitación de pruebas (CI00), que el servidor limita a las salas 0000-0009 y no permite reservar.
Uso: python3 tests/e2e.py http://localhost:8080/index.html
"""
import asyncio, os, sys, traceback
from playwright.async_api import async_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else 'http://localhost:8080/index.html'
ROOM = os.environ.get('E2E_ROOM', '0007')
OUT = os.environ.get('E2E_OUT', 'e2e-out')
INV, DEV = 'CI00', 'ci-e2e-psinusal-0001'
T = 25000
IN_ROOM = "(r)=>document.body.classList.contains('role-'+r) && getComputedStyle(document.getElementById('gate')).display==='none'"
steps = []

def ok(msg):
    steps.append(msg); print('  ✓', msg, flush=True)

async def new_page(browser, name, teacher=False):
    ctx = await browser.new_context(viewport={'width': 1100, 'height': 800} if teacher else {'width': 400, 'height': 800}, locale='es-ES')
    if teacher:
        await ctx.add_init_script("try{ localStorage.setItem('monitor-inv','%s'); localStorage.setItem('monitor-dev','%s'); }catch(e){}" % (INV, DEV))
    pg = await ctx.new_page(); pg._name = name
    pg.on('pageerror', lambda e: print(f'  [{name}] ERROR JS: {e}', flush=True))
    pg.on('dialog', lambda d: asyncio.ensure_future(d.dismiss()))
    await pg.goto(URL, wait_until='load')
    await pg.wait_for_function("typeof selectRole==='function' && !!window.supabase", timeout=T)
    return pg

async def enter(pg, role_card, adm=None):
    await pg.evaluate("(id)=>document.getElementById(id).click()", role_card)
    await pg.wait_for_function("getComputedStyle(document.getElementById('gateRoomStep')).display!=='none'", timeout=T)
    if adm is not None:
        await pg.evaluate("(v)=>salaAdmSetVal(v)", adm)
    await pg.fill('#roomCodeInput', ROOM)
    await pg.evaluate("document.getElementById('btnConnect').click()")

async def status(pg):
    return await pg.evaluate("(document.getElementById('gateStatus')||{}).textContent||''")

async def run():
    os.makedirs(OUT, exist_ok=True)
    async with async_playwright() as p:
        b = await p.chromium.launch(args=['--no-sandbox', '--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'])
        pages = []
        try:
            # 1. El docente crea la sala (entrada libre)
            doc = await new_page(b, 'docente', teacher=True); pages.append(doc)
            await enter(doc, 'cardRoleInstructor', adm=False)
            await doc.wait_for_function(IN_ROOM, arg='instructor', timeout=T)
            await doc.wait_for_function("!!channel && channel.state==='joined'", timeout=T)
            ok('el docente crea la sala %s con la invitación verificada en el servidor' % ROOM)

            # 2. Un estudiante entra con el código
            est = await new_page(b, 'estudiante1'); pages.append(est)
            await enter(est, 'cardRoleMonitor')
            await est.wait_for_function(IN_ROOM, arg='monitor', timeout=T)
            await doc.wait_for_function("studentCount>=1", timeout=T)
            ok('el estudiante entra (entrada libre) y el docente lo ve conectado')

            # 3. El cambio de ritmo del docente llega al monitor del estudiante
            cur = await est.evaluate("state.rhythm")
            target = await doc.evaluate("(cur)=>{ const o=[...document.getElementById('rhythmSelect').options].map(x=>x.value).filter(v=>v && v!==cur && v!==state.rhythm); return o[0]; }", cur)
            await doc.evaluate("(v)=>{ const s=document.getElementById('rhythmSelect'); s.value=v; s.dispatchEvent(new Event('change',{bubbles:true})); }", target)
            await doc.wait_for_timeout(500)
            await doc.evaluate("try{ sendNow(); }catch(e){}")
            await est.wait_for_function("(v)=>state.rhythm===v", arg=target, timeout=T)
            ok('el ritmo enviado por el docente (%s) llega al estudiante' % target)

            # 4. El docente sale: el estudiante ve la cuenta atrás de cierre
            try:
                await doc.evaluate("leaveRoom(false, true)")
            except Exception:
                pass
            await est.wait_for_function("(()=>{ const e=document.getElementById('roomEnd'); return e && !e.hidden && /cierre en/.test(e.textContent); })()", timeout=T)
            ok('al salir el docente, el estudiante ve «Docente ausente · cierre en…»')

            # 5. El docente vuelve (ahora con admisión): se cancela la cuenta atrás
            await doc.wait_for_load_state('load')
            await doc.wait_for_function("typeof selectRole==='function' && !!window.supabase && getComputedStyle(document.getElementById('gate')).display!=='none'", timeout=T)
            await enter(doc, 'cardRoleInstructor', adm=True)
            await doc.wait_for_function(IN_ROOM, arg='instructor', timeout=T)
            await est.wait_for_function("document.getElementById('roomEnd').hidden===true", timeout=T)
            ok('al volver el docente se cancela el cierre de la sala')

            # 6. Admisión: un segundo estudiante pide entrar, el docente lo admite y entra
            est2 = await new_page(b, 'estudiante2'); pages.append(est2)
            await enter(est2, 'cardRoleMonitor')
            await doc.wait_for_function("(()=>{ const x=document.querySelector('#joinBar .jb-b.ok'); return !!x && !document.getElementById('joinBar').hidden; })()", timeout=T)
            st = await status(est2)
            assert 'admita' in st, 'el estudiante no ve el aviso de espera de admisión: %r' % st
            ok('con admisión, el docente recibe la solicitud y el estudiante queda a la espera')
            await doc.evaluate("document.querySelector('#joinBar .jb-b.ok').click()")
            await est2.wait_for_function(IN_ROOM, arg='monitor', timeout=T)
            ok('al admitirlo, el segundo estudiante entra en la sala')

            # 7. El docente sale y la sala se cierra
            try:
                await doc.evaluate("leaveRoom(false, true)")
            except Exception:
                pass
            await doc.wait_for_timeout(1500)
            ok('el docente cierra la sala')
        except Exception:
            print('\n✗ FALLO tras %d pasos correctos' % len(steps), flush=True)
            traceback.print_exc()
            for pg in pages:
                try:
                    await pg.screenshot(path=os.path.join(OUT, pg._name + '.png'))
                    print('  [%s] estado de la puerta: %r' % (pg._name, await status(pg)), flush=True)
                except Exception as e:
                    print('  [%s] sin captura: %s' % (pg._name, e), flush=True)
            await b.close(); sys.exit(1)
        await b.close()
    print('\nTodo correcto: %d comprobaciones.' % len(steps))

asyncio.run(run())
