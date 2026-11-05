# Роутер: тип цели → первый ход

Грузится, только когда цель — не сайт. Адаптировано из `aicodeking/leanrea` (`leanrea/SKILL.md`,
коммит 17.10.2026, MIT, © AICodeKing — текст лицензии ниже). Наше: сокращено под macOS, версии `npx`
закреплены, раздел «сайт» заменён своим `web-trace.py`.

## Правила
- Работать на копии: `cp -c` (APFS, мгновенно) в `<scratch>/re/<имя>`. Готовая распаковка есть — переиспользовать.
- Сначала `file -b T` и магические байты, потом самая дешёвая опись, только потом дизассемблер.
- `rg` пропускает скрытые папки, а сборщики прячут код в `.webpack/`, `.vite/` → всегда `rg -uu`.
- Большие и минифицированные файлы не читать целиком: `rg -o '.{0,200}X.{0,200}' F | head`.
  Дизассемблер — один раз в файл, дальше греп по файлу.
- Независимые пробы — одним вызовом shell через `;` и маркеры `echo '--- имя'`
  (в кавычках: zsh ломает голое `==x`). Каждый вывод ограничить `head` / `-m`.
- Ходы дороже байтов: цель — 2–4 вызова (одна разведка пачкой, один-два среза, ответ).
- Нет инструмента — не блокер: запасной путь в той же строке; `brew install` только с разрешения владельца компьютера.

## Первые ходы (одним вызовом)

Mach-O / нативный модуль `.node`:
```sh
T=./bin; D=$S/re/$(basename $T); mkdir -p $D; file $T; lipo -info $T; otool -L $T; echo '--- EXPORTS'; nm -gU $T | head -150; echo '--- IMPORTS'; nm -u $T | rg -v '^__Z|napi_' | tr '\n' ' '; echo; echo '--- STRINGS'; strings -a -n 6 $T | rg -v '^[_$.]' | head -400; otool -tV $T > $D/dis.txt; echo '--- FUNCS'; rg '^[_a-zA-Z].*:$' $D/dis.txt | head -150
```
Одна функция: `awk '/^FUNC:$/{p=1;next} p&&/^[_a-zA-Z].*:$/{exit} p' $D/dis.txt | rg 'bl|b\.|cmp|literal'`.

Electron (`*.app/Contents/Resources/app.asar`):
```sh
A=./app.asar; X=$S/re/asar; rm -rf "$X"; npx -y @electron/asar@3.2.17 extract $A $X && jq '{name,version,main}' $X/package.json; find $X -path '*/node_modules' -prune -o -type f \( -name '*.js' -o -name '*.node' \) -print | head -60; ls ${A}.unpacked/node_modules 2>/dev/null; rg -uu -l --glob '!node_modules' 'exposeInMainWorld' $X
```
Дальше иголка по бандлу: `rg -uu -o --glob '!node_modules' '.{0,250}ИГОЛКА.{0,250}' $X | head -20`.
IPC: `rg -uu -n -o '.{0,120}(ipcMain\.(handle|on)|ipcRenderer\.(invoke|send)|contextBridge\.exposeInMainWorld|preload:).{0,160}' $X --glob '!node_modules'`
и `rg -uu -o '"[a-z]+:[a-z0-9:-]+"' $X/<main> | sort -u`.

## Таблица

| Цель (как узнать) | Самый быстрый путь |
|---|---|
| Сайт, лендинг, виджет | **не отсюда** — `tools/web-trace.py` |
| Минифицированный JS | `sourceMappingURL` → если в карте есть `sourcesContent`, исходники целиком (`jq -r '.sources[]'`). Иначе срезы `rg -o`. |
| Mach-O | `lipo -info`; `otool -L`; `xcrun dyld_info -exports -imports`; `nm -gU` / `nm -u` (импорты = карта возможностей); `strings -a -n 6`; `codesign -d --entitlements :- T`; `otool -ov` (ObjC). |
| Логика нативного кода | `otool -tV T > dis.txt`, найти функцию, читать цепочку вызовов и строк. Псевдокод — только если не хватило: Ghidra `analyzeHeadless` или `r2 -qc 'aaa; pdc @ sym.X'` (у нас не стоят). |
| Go (`.go.buildinfo`) | `go version -m T`; без Go: `strings -a T \| rg -m1 -A200 'Go buildinf'`. |
| Rust | `strings -a T \| rg -o '[a-z0-9_-]+-[0-9]+\.[0-9]+\.[0-9]+/src' \| sort -u`; компилятор `rg -o '/rustc/[0-9a-f]{40}'`. |
| .NET | `ilspycmd -p -o X T.dll`, затем `rg`; P/Invoke — `rg DllImport`. |
| Android APK | `unzip -l a.apk` быстро; `jadx -d X a.apk` + `rg`; ресурсы — `apktool d --no-src`. `libflutter.so` = Flutter, `libhermes.so` = React Native. |
| SQLite | копия базы + `-wal`; `sqlite3 'file:C?mode=ro' '.schema'`, потом `SELECT … LIMIT 20`. |
| plist / ассеты | `plutil -p F` (bundle id, URL-схемы, строки приватности); `assetutil --info Assets.car`. |
| HAR / pcap | HAR: `jq -r '.log.entries[].request \| .method+" "+.url'`; pcap: `tshark -r C -T fields -e dns.qry.name -e tls.handshake.extensions_server_name \| sort -u`. |
| Работающее приложение | `log stream --predicate 'process=="X"'`, `fs_usage`; Electron main `--inspect` — только если фьюзы разрешают (`npx -y @electron/fuses@1.8.0 read --app A.app`). |

Перед первым ходом проверь одной командой, что из этого стоит у тебя: `which jq otool nm strings sqlite3 rg go jadx apktool r2 tshark`. Чего нет — запасной путь из той же строки таблицы.

---

MIT License — Copyright (c) 2026 AICodeKing. Permission is hereby granted, free of charge, to any person
obtaining a copy of this software and associated documentation files (the "Software"), to deal in the
Software without restriction, including without limitation the rights to use, copy, modify, merge, publish,
distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions: The above copyright notice and this permission
notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED
"AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED.
