from playwright.sync_api import sync_playwright
from pathlib import Path
import json
P=Path('/workspace/shared/twinteams/design-artifacts')
with sync_playwright() as p:
 browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
 page=browser.new_page(viewport={'width':1440,'height':1100},device_scale_factor=1)
 errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
 page.goto((P/'Twinteams-Database-ER-Fields-v1.html').as_uri(),wait_until='load')
 page.screenshot(path=str(P/'er-desktop-preview.png'),full_page=False)
 page.locator('#er-replies').scroll_into_view_if_needed();page.screenshot(path=str(P/'er-relationships-preview.png'),full_page=False)
 page.locator('#table-agent_sessions').scroll_into_view_if_needed();page.screenshot(path=str(P/'er-fields-preview.png'),full_page=False)
 dims=page.locator('svg').evaluate_all('(xs)=>xs.map(x=>({w:x.getBoundingClientRect().width,h:x.getBoundingClientRect().height,vb:x.getAttribute("viewBox")}))')
 page.locator('#filter').fill('agent_sessions')
 visible=page.locator('.table-card:not(.hide)').count()
 page.locator('#filter').fill('');page.set_viewport_size({'width':390,'height':844});page.goto((P/'Twinteams-Database-ER-Fields-v1.html').as_uri(),wait_until='load')
 page.screenshot(path=str(P/'er-mobile-preview.png'),full_page=False)
 mobile=page.evaluate('({w:innerWidth,doc:document.documentElement.scrollWidth})')
 print(json.dumps({'errors':errors,'svg_dimensions':dims,'search_visible':visible,'mobile':mobile},ensure_ascii=False))
 assert not errors
 assert visible==1,visible
 assert mobile['doc']==mobile['w'],mobile
 browser.close()
