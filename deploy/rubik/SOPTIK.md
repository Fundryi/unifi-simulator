# Předání Soptíkovi: UniFi emulator na RUBIKu

## Úkol a schválený rozsah

Nasaď na **RUBIK** do Dockeru tři emulace pod
**`/mnt/appdata/containers/unifi-emulator`**. Uživatel potvrdil funkční testy
souběžného US8 + UGW3 a CRS326 a požádal o produkční nasazení. Produkční UniFi
Network je **jiný controller než testovací**. Použij proto **nové syntetické
identity a novou adopci**, bez přenosu testovacích `identity.json`, klíčů nebo volumes.

| Compose služba | UniFi role | Read-only RouterOS target | Host state adresář |
| --- | --- | --- | --- |
| `emulator` | US8: porty, klienti, sousedé RB | RB760iGS `192.168.0.249:8728`, RouterOS 7.24.4 | `state/rb760-us8` |
| `emulator-gateway` | UGW3: WAN RB, výchozí `ether1` | stejný RB, stejný API účet/secret | `state/rb760-ugw3` |
| `emulator-crs326` | Switch Pro 24: 24×1G + 2×SFP+, klienti | CRS326 `192.168.0.251:8728`, stabilní RouterOS 7.24+ | `state/crs326` |

Všechny MikroTik IP zůstávají stejné jako v testech. Názvy jejich rozhraní ověř
proti mapám `repo/examples/routeros-rb760igs.json` a `routeros-crs326.json`;
testy uživatel potvrdil funkční, bez důvodu mapy neměň. Profil UGW3 neobsahuje LAN
porty/klienty: tuto roli dál zajišťuje samostatný US8. UniFi zobrazuje dvě logická
zařízení pro jeden fyzický RB; jejich provoz nesčítej jako nezávislé routery.
Nevytvářej mezi nimi smyšlenou LLDP/topologickou vazbu.

Nenasazuj lab controller, MongoDB ani syntetický scénář. Nepoužívej `deploy/lab`
overrides v této instalaci. Produkční projekt má jeden vlastní Compose soubor,
externí Docker síť a bind mounts v požadované appdata složce. Neprováděj žádné
controllerem zaslané VLAN/firewall/NAT/DHCP změny na MikroTiku; aplikace je ignoruje.
Aplikace zůstává experimentální; konkrétní cílovou verzi UniFi zaznamenej a ověř.
Dosavadní controller testy byly na 10.6.106, uživatel potvrdil testovací nasazení.

## Nejdřív zjisti lokální podmínky

Na RUBIKu zjisti konkrétní produkční **`/inform` URL**, verzi controlleru, jeho site,
existující Docker sítě, jejich IPAM a tři rezervované volné IPv4 adresy. Tyto údaje
nebyly v zadání dodány; neodvozuj je z IP MikroTiků a nekopíruj síť `unifi-lab` ani
adresy `172.30.88.*` naslepo. Pokud je nelze zjistit z dostupné konfigurace, vyžádej
si jen chybějící URL/síť/adresy. Hesla a adopční klíče nikdy nevyžaduj do chatu.

- Je-li controller na stejném Docker hostu, preferuj vhodnou existující uživatelskou
  bridge síť sdílenou s controllerem a jeho stabilní DNS alias. Síť musí mít IPv4 IPAM
  a musí jít z ní přistupovat na oba MikroTiky.
- Je-li controller mimo RUBIK, zvol existující síť se třemi adresami dostupnými
  controlleru, například správně připravenou LAN ipvlan/macvlan síť. Nenastavuj do
  payloadu LAN IP, kterou kontejner ve skutečnosti nemá. U hosta/controlleru na
  RUBIKu ověř případnou izolaci macvlan od hosta. Nepoužívej host networking pro
  tento Compose; jeho smlouvou jsou tři skutečné oddělené adresy.
- IP zapiš shodně do síťové konfigurace a inform payloadu; dodaný Compose používá
  jednu proměnnou pro obojí. Ověř rezervace/DHCP i Docker alokace, samotný neúspěšný
  ping neprokazuje volnou IP. Neměň stávající sítě jiných služeb.
- Controller musí být v běžném nakonfigurovaném stavu. Ověř přístup a oprávnění pro
  adopci. UniFi dovoluje jeden gateway v site: pokud už má cílová site jiný gateway,
  **neodebírej ho**. Nasaď US8/CRS samostatně a oznam konkrétní blokaci UGW3 k rozhodnutí
  o jiné site nebo jiné reprezentaci. Nepřemisťuj zařízení ani nemaž controller DB.
- Použij existující účty pouze s `read,api`; RB účet sdílejí oba RB kontejnery, CRS
  má samostatný secret. Transport zůstává explicitně API/8728 bez SSL stejně jako
  v potvrzeném testu; heslo tímto spojením jde v plaintextu. Nerozšiřuj oprávnění.
  API allowed-address a firewall musí povolit **skutečný zdroj po Docker NAT** nebo
  přímo LAN adresy kontejnerů. Dřívější problém s uzavřením `/login` způsobil právě
  nesprávný allowed-address rozsah. Ověř ho z kontejneru, ne pouze z hosta.

Při diagnostice používej výběr konkrétních polí, například Docker názvy, IPAM,
network attachments a health. Nevypisuj celý `docker inspect`, `.env`, secret
soubory ani syrové UniFi device API odpovědi: mohou obsahovat klíče/hesla.

## Rozložení a připnutí zdroje

```text
/mnt/appdata/containers/unifi-emulator/
├── compose.yml
├── .env                         # bez hesel, mode 0600
├── repo/                        # připnutý checkout MartinDejmal/unifi-simulator
├── .secrets/                    # mode 0700, nikdy do Git ani build contextu
│   ├── routeros-rb760-password  # pouze API heslo + LF, mode 0444
│   └── routeros-crs326-password # pouze API heslo + LF, mode 0444
├── state/                       # per-device UID/GID 10001:10001, mode 0700
│   ├── rb760-us8/
│   ├── rb760-ugw3/
│   └── crs326/
└── backups/                     # mode 0700, obsahuje citlivé identity
```

Použij schválený **plný commit SHA ze zprávy předání**, ne automatické budoucí
`main`/`latest`. Repozitář je `https://github.com/MartinDejmal/unifi-simulator.git`.
Současné runtime chování je stejné jako v uživatelem potvrzeném commitu `b2acad8`;
nový balíček přidává produkční Compose a tento návod. Zaznamenej skutečný SHA,
Docker image ID, verzi controlleru a datum nasazení.

Příkazy níže vyžadují Docker Compose v2 a Linux shell na RUBIKu s oprávněním
spravovat Docker a appdata.
Proměnná `release_sha` je schválený SHA, nikoli heslo. Pokud cílová složka/checkout
již existují, nejprve je inventarizuj a zazálohuj; nic nepřepisuj naslepo.

```sh
install -d -m 0750 /mnt/appdata/containers/unifi-emulator
cd /mnt/appdata/containers/unifi-emulator
# Nastav release_sha na SHA z předání.
git clone --no-checkout https://github.com/MartinDejmal/unifi-simulator.git repo
git -C repo checkout --detach "$release_sha"
install -m 0644 repo/deploy/rubik/compose.yml compose.yml
install -m 0600 repo/deploy/rubik/.env.example .env
install -d -m 0700 .secrets backups
install -d -m 0750 state
install -d -m 0700 -o 10001 -g 10001 state/rb760-us8 state/rb760-ugw3 state/crs326
```

Repo/examples musí být čitelné UID 10001 (veřejné mapy 0644). Stavy jsou bind mounts;
Docker je sám nevytváří. Na již existující state adresáře ani jejich obsah nepouštěj
rekurzivní opravy oprávnění bez zjištění vlastníka a zálohy. V čerstvých adresářích
nesmí být zkopírovaná testovací identita; nic z testů neimportuj.

Do `.env` bezpečně doplň požadované ne-tajné hodnoty podle `.env.example`, zejména:
`MTUNIFI_RELEASE` = přesný výstup `git -C repo rev-parse HEAD`, inform URL, Docker síť,
tři volné adresy, dva API usernames. `MTUNIFI_ROUTEROS_WAN_INTERFACE=ether1` ponech.
Nepoužívej labový `MTUNIFI_HOSTNAME` ani `MTUNIFI_GATEWAY_IP=.12` bez ověření sítě;
produkční hostnames a role jsou explicitně dané v Compose.

API hesla získej existujícím bezpečným způsobem. Lze přenést **jen API password
soubory** z testu: `routeros-password` → `routeros-rb760-password` a
`routeros-crs326-password` → stejnojmenný produkční soubor. Uživatelská jména musí
odpovídat. Nepřenášej `identity.json`/`telemetry.json` ani testovací adopční volume.
Ulož pouze heslo jako UTF-8 bez BOM s koncovým LF. Soubory nastav na 0444, rodičovskou
`.secrets` ponech 0700; to dovolí UID 10001 číst file-secret mount a hostí přístup
zůstává chráněn rodičem. `.env` neobsahuje hesla. Compose secrets jsou lokální bind
soubory, ne šifrovaný trezor; hostí úložiště a zálohy chraň odpovídajícím způsobem.

## Kontrola a sestavení

Všechny další příkazy prováděj z `/mnt/appdata/containers/unifi-emulator`.
Nepoužívej `-f repo/deploy/rubik/compose.yml`: relativní cesty jsou pro appdata root.

```sh
docker compose config --quiet
docker compose config --services
# Všechny tři služby používají tutéž image; sestav ji jednou.
docker compose build emulator
```

Musí být tři služby: `emulator`, `emulator-gateway`, `emulator-crs326`. Nejsou tu
publikované porty, Docker socket, privileged/host režim ani controller/MongoDB
služby či jejich datové mounts. Zvol aplikační síť, nikoli síť vyhrazenou databázi.
Kontejnery běží jako UID 10001 s read-only root filesystemem, bez capabilities,
no-new-privileges, rotací logů, restart policy a healthcheckem z image.

Build zachovává TLS ověřování a zamčené závislosti. Na běžném RUBIKu se nepoužívá
cloud CA override; pokud má vlastní důvěryhodnou proxy CA, předej ji podporovaným
BuildKit secretem, nevypínej ověřování. Značka image je `unifi-emulator:<Git SHA>`.
Dockerfile základní image není připnutá digestem; pro přesný rollback zachovej také
**již sestavenou image a její ID**, nepovažuj znovusestavení starého SHA za identický
binární obraz a nepouštěj automatický image prune.

Dokud služby neběží, ověř každý API profil jednorázovým probe. Probe nekontaktuje
controller, nevytváří identitu ani neposílá syntetická data:

```sh
docker compose run --rm --no-deps emulator probe-routeros
docker compose run --rm --no-deps emulator-gateway probe-routeros
docker compose run --rm --no-deps emulator-crs326 probe-routeros
```

Očekávej RB 6 fyzických portů v 8 slotech, CRS 26 portů se SFP+ na 25/26 a
UGW3 WAN `ether1` s reálnými RX/TX čítači; gateway má 0 switchových portů. Unknown
IP/speed/neighbor nejsou nula ani důvod data vymýšlet. Konkrétní optional warning
zaznamenej; required API failure vyřeš před startupem.

Jakmile služby běží, používej **`exec` místo `run`** pro další probes, protože
jednorázový kontejner by soutěžil o stejnou statickou IP.

## Přepnutí a adopce

1. Po úspěšném probe připrav přepnutí z testů. Původní testovací state/controller
   data ponech pro návrat. Máš-li testovací host dostupný, zastav původní emulátory;
   jinak uveď v reportu, že je má po ověření produkce zastavit jejich vlastník.
   Protože jde o nové identity a jiný controller, krátký souběh read-only testů
   neblokuje adopci; jen přidává API polling. Stejnou kopii identity na dvou hostech
   nespouštěj. Žádný MikroTik se nerestartuje.
2. Spusť pouze tento nový projekt. Pokud site blokuje druhý gateway, spusť zatím
   jen `emulator emulator-crs326` a oznam blokaci; stávající gateway nemaž.

   ```sh
   docker compose up -d --no-build --pull never
   docker compose ps
   docker compose logs --tail 80
   ```

3. V produkčním controlleru adoptuj **tři nové syntetické identity**:
   `mtunifi-rb760-us8`, `mtunifi-rb760-ugw3`, `mtunifi-crs326`, do určené site.
   Použij již dostupný autorizovaný přístup do UI/API, hesla/keys nevypisuj. Před
   akcí se ujisti podle hostname/MAC, že adoptuješ právě tyto nové emulace, ne jiná
   fyzická zařízení čekající na adopci. Nepoužívej `reset-adoption` jako součást
   prvního nasazení: prázdné state adresáře ještě žádnou identitu nemají.
4. První factory inform může vrátit 404 a chvíli čekat na adopci. Pak očekávej nový
   soukromý klíč, GCM a `exchanging`. Startup bez chyby ani health neprokazují Connected;
   ověř controller UI/API. Po adopci můžeš bezpečně zaznamenat syntetické MAC,
   ale nikdy samotný `authkey` nebo syrovou device API odpověď.

## Přejímka a report

```sh
docker compose exec -T emulator mtunifi diagnose
docker compose exec -T emulator-gateway mtunifi diagnose
docker compose exec -T emulator-crs326 mtunifi diagnose
docker compose exec -T emulator mtunifi health
docker compose exec -T emulator-gateway mtunifi health
docker compose exec -T emulator-crs326 mtunifi health
```

Ověř všechny tři Connected v produkčním controlleru alespoň 5 minut, rostoucí čítače,
RB porty/klienty a CRS 26 slotů/SFP+ rychlosti podle skutečné linky. U UGW3 ověř WAN
IP, stav linky a sazby přes více informů: **RX download, TX upload**. UniFi alias
WAN je `eth0`, zdroj v diagnostice zůstává `ether1`. Link-up není důkaz internetu;
žádná latency/DPI/speedtest data se nevyrábějí.

Jednou proveď `docker compose restart`, potom znovu ověř health a Connected.
Syntetické MAC i klíče musí zůstat ve stejných třech adresářích a komunikace pokračovat
v GCM bez nové adopce. Klíče porovnávej jen lokálně jako boolean/shodu, ne jako obsah
výstupu. Test musí skončit ověřeným stavem, ne jen odeslaným restartem.

Do reportu uveď cestu, source SHA a image ID, síť/tři adresy, inform URL/verzi/site,
profily/targety/WAN, stav adopce a health po restartu, optional warnings, umístění
zálohy a skutečný stav testovacích kontejnerů (zastavené, nebo předané vlastníkovi
k zastavení). Ukaž, co bylo skutečně
ověřeno; pokud chybí controller přístup nebo vhodná síť, dokonči nezávislou přípravu
bez spuštění nesprávně nakonfigurovaných zařízení a konkrétní překážku vypiš.

## Aktualizace, záloha a návrat

Pro běžné build/run/probe/diagnose používej jediný produkční Compose z appdata root.
Žádné lab overrides. Před změnou runtime verze zastav jen tento projekt, ulož privátní
kopii `compose.yml`, `.env` a konzistentní zálohu všech tří state adresářů se zachováním
UID/GID a mode 0600 souborů. API secrets zálohuj podle existujícího bezpečného režimu.
Nikdy je nepřidávej do Git, veřejného issue ani do nechráněného logu. Aktuální image
ponech lokálně. Nepoužívej `down -v`, nemaž `state` a neodpojuj cizí sítě.

Pro rollback nové verze vrať předchozí Compose/`.env`, připnutý checkout a **původní
lokální image**; podle kompatibility vrať odpovídající state backup jen při zastavených
službách. Spusť `docker compose up -d --no-build --pull never`, ověř health/Connected.
Neprováděj automatickou aktualizaci z `main` ani automatické mazání starých image.

Pro návrat z prvního produkčního nasazení na testy: zastav produkční projekt, jeho
čerstvé state a image ponech, znovu spusť původní testovací projekt s původními klíči
pro původní testovací controller. Neimportuj do něj nové produkční klíče. Odebrání
nových emulací z produkčního UI je případný samostatný cílený krok podle jejich MAC;
neodebírej skutečná UniFi zařízení. Pokud se později produkční emulace z controlleru
záměrně smaže, pro readopci zastav/resetuj pouze její službu tímto produkčním Compose.
Reset zachová původní controller/profile/RouterOS binding a nehodí se pro změnu
controlleru. Změna controlleru znovu vyžaduje oddělenou identitu/state.
