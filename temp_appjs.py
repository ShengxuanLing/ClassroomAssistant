import pathlib

p = pathlib.Path('src/web/app.js')
s = p.read_text(encoding='utf-8')

old = (
    " *   #/review            #/review, #/courses/<c>/review\n"
    " *   #/                  #/\n\n"
    " *   #/materials         #/materials\n"
    " *   #/reviews           #/reviews\n"
    " *   #/courses           #/courses\n"
    " *   #/exercises         #/exercises, #/courses/<c>/exercises/<e>/<sid>\n"
    " *   #/mistakes          #/mistakes, #/mistakes/<c>/<k>\n"
    " *   #/review-pack       #/review-pack\n"
    " *\n"
    " *   清空"
)

new = (
    " *   #/review            #/review, #/courses/<c>/review\n"
    " *   #/                  #/\n\n"
    " *   #/materials         #/materials\n"
    " *   #/reviews           #/reviews\n"
    " *   #/courses           #/courses\n"
    " *   #/exercises         #/exercises, #/courses/<c>/exercises/<e>/<sid>\n"
    " *   #/mistakes          #/mistakes, #/mistakes/<c>/<k>\n"
    " *   #/review-pack       #/review-pack\n"
    " *\n"
    " * 2026-10-15: 右侧四个页面（考前复习 / 复习包 / 练习 / 错题本）的**顶栏入口**\n"
    " * 已改为无 href 的 <span class=\"nav-disabled\">，不可点击、不发路由、不进 active。\n"
    " * 但**路由分支与页面函数原样保留** —— 上面四行的归属关系不变，深链仍可达。\n"
    " * 解禁时把四个 <span> 换回 <a href> 即可，本表无需改动。\n"
    " *\n"
    " *   清空"
)

if old not in s:
    print('OLD NOT FOUND')
else:
    s = s.replace(old, new)
    p.write_bytes(s.encode('utf-8'))
    print('OK')