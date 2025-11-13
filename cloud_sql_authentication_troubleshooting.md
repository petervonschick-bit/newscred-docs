# Cloud SQL MySQL Autentikációs Probléma Megoldása PyMySQL-lel

## 📋 Összefoglaló

Ez a dokumentum egy valós production környezetben tapasztalt Cloud SQL autentikációs problémát és annak megoldását dokumentálja. A probléma PyMySQL könyvtár és MySQL 8.0 alapértelmezett autentikációs plugin inkompatibilitásából eredt.

---

## 🔍 A Probléma

### Környezet
- **Platform:** Google Cloud Platform (GCP)
- **Adatbázis:** Cloud SQL MySQL 8.0
- **VM:** Google Compute Engine (Ubuntu 22.04)
- **Framework:** Flask + Gunicorn
- **MySQL Client:** PyMySQL
- **Kapcsolat típus:** Private IP (VPC)

### Tünetek

```python
# Hiba üzenet
pymysql.err.OperationalError: (1045, "Access denied for user 'tutitipp'@'10.128.0.2' (using password: YES)")
```

**Jellemzők:**
- ✅ MySQL CLI-ből sikeres kapcsolódás ugyanazokkal a hitelesítő adatokkal
- ❌ PyMySQL-ből "Access Denied" hiba
- ✅ Jelszó és username biztosan helyes
- ✅ User létezik `@'%'` (wildcard) host pattern-nel

---

## 🕵️ Diagnózis

### 1. Kezdeti hipotézisek

**Hipotézis #1: Host-based access control probléma**
```sql
-- Ellenőrzés
SELECT user, host FROM mysql.user WHERE user = 'tutitipp';

-- Eredmény
+----------+------+
| user     | host |
+----------+------+
| tutitipp | %    |  -- Wildcard, minden IP-ről engedélyezett
+----------+------+
```
❌ **Kizárva** - A wildcard minden IP-t engedélyez

**Hipotézis #2: Jelszó vagy konfiguráció hiba**
```bash
# MySQL CLI teszt
mysql -h 10.65.240.3 -u tutitipp -p'Tutitipp@2025' newscred -e "SELECT 1"
# ✅ Sikeres!

# PyMySQL teszt (Flask app)
# ❌ Access Denied
```
❌ **Kizárva** - CLI működik, tehát jelszó helyes

**Hipotézis #3: Autentikációs plugin inkompatibilitás**
✅ **BINGO!** - Ez volt a probléma

### 2. Root Cause Analysis

MySQL 8.0 alapértelmezetten a `caching_sha2_password` autentikációs plugint használja, amely biztonságosabb, de kompatibilitási problémákat okozhat régebbi kliensekkel.

```sql
-- User autentikációs plugin ellenőrzése
SELECT user, host, plugin FROM mysql.user WHERE user = 'tutitipp';

+----------+------+-----------------------+
| user     | host | plugin                |
+----------+------+-----------------------+
| tutitipp | %    | caching_sha2_password |  -- MySQL 8.0 alapértelmezett
+----------+------+-----------------------+
```

**Mi történt:**

1. **MySQL CLI (modern):** Támogatja a `caching_sha2_password` plugint
   - Sikeres autentikáció ✅

2. **PyMySQL (alkalmazás):** Nem támogatja megfelelően vagy nem tudja végrehajtani a teljes handshake-et
   - Sikertelen autentikáció ❌
   - Félrevezető hibaüzenet: "Access Denied"

**Kulcs insight:** A hibaüzenet (`Access Denied`) **nem** azt jelenti, hogy a jelszó rossz, hanem azt, hogy az autentikációs *mechanizmus* nem kompatibilis.

---

## ✅ Megoldások

### Megoldás A: Autentikációs Plugin Váltás (Nem javasolt)

```sql
-- mysql_native_password használata (régebbi, kevésbé biztonságos)
ALTER USER 'tutitipp'@'%' IDENTIFIED WITH mysql_native_password BY 'Tutitipp@2025';
FLUSH PRIVILEGES;
```

**Előnyök:**
- Gyors fix
- PyMySQL azonnal működik

**Hátrányok:**
- Kevésbé biztonságos autentikáció
- Visszalépés a biztonsági szabványokban
- Cloud SQL managed service-ben lehet, hogy nem engedélyezett

⚠️ **Cloud SQL korlátozás:** Sok esetben a Cloud SQL nem engedi az autentikációs plugin manuális módosítását Console-on keresztül létrehozott userekre.

---

### Megoldás B: Cloud SQL Auth Proxy (✅ Javasolt)

A **Cloud SQL Auth Proxy** egy Google által biztosított proxy szerver, amely:
- Kezeli az IAM-alapú autentikációt
- Automatikus SSL/TLS encryption
- Támogatja a modern MySQL 8.0 autentikációt
- Kliensek localhost-on keresztül csatlakoznak

#### Lépések

**1. VM Service Account Jogosultságok**

```bash
# Service Account email azonosítása
gcloud compute instances describe VM_NAME --zone=ZONE --format="value(serviceAccounts[0].email)"

# IAM role hozzáadása
gcloud projects add-iam-policy-binding PROJECT_ID \
  --member='serviceAccount:SERVICE_ACCOUNT_EMAIL' \
  --role='roles/cloudsql.client'
```

**2. VM Access Scopes Beállítása**

⚠️ **Kritikus:** A VM-nek megfelelő API access scope-pal kell rendelkeznie!

```bash
# VM leállítása
gcloud compute instances stop VM_NAME --zone=ZONE

# Scope módosítása
gcloud compute instances set-service-account VM_NAME \
  --zone=ZONE \
  --scopes=https://www.googleapis.com/auth/cloud-platform

# VM indítása
gcloud compute instances start VM_NAME --zone=ZONE
```

**VAGY Cloud Console-ban:**
1. VM instances → VM_NAME → **STOP**
2. **EDIT**
3. Cloud API access scopes → **Allow full access to all Cloud APIs**
4. **Save** → **START**

**3. Cloud SQL Auth Proxy Telepítése**

```bash
# Letöltés
wget https://dl.google.com/cloudsql/cloud_sql_proxy.linux.amd64 -O /tmp/cloud_sql_proxy
chmod +x /tmp/cloud_sql_proxy

# Indítás TCP módban
/tmp/cloud_sql_proxy --instances=PROJECT_ID:REGION:INSTANCE_NAME=tcp:0.0.0.0:3307 &
```

**4. Alkalmazás Konfiguráció Módosítása**

```json
// db.json - ELŐTTE (Direct connection)
{
  "host": "10.65.240.3",  // Private IP
  "port": 3306,
  "user": "root",
  "password": "PASSWORD",
  "database": "newscred",
  "charset": "utf8mb4"
}

// db.json - UTÁNA (Proxy connection)
{
  "host": "127.0.0.1",    // Localhost - proxy végpont
  "port": 3307,           // Proxy port
  "user": "root",
  "password": "PASSWORD",
  "database": "newscred",
  "charset": "utf8mb4"
}
```

**5. Tesztelés**

```bash
# MySQL CLI teszt proxy-n keresztül
mysql -h 127.0.0.1 -P 3307 -u root -p'PASSWORD' newscred -e "SELECT 1"

# PyMySQL teszt (alkalmazás)
# ✅ Most már működik!
```

**6. Production Setup - Systemd Service**

```ini
# /etc/systemd/system/cloud-sql-proxy.service
[Unit]
Description=Cloud SQL Proxy
After=network.target

[Service]
Type=simple
User=YOUR_USER
ExecStart=/usr/local/bin/cloud_sql_proxy \
  --instances=PROJECT_ID:REGION:INSTANCE_NAME=tcp:0.0.0.0:3307
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
```

```bash
# Service engedélyezése és indítása
sudo systemctl daemon-reload
sudo systemctl enable cloud-sql-proxy
sudo systemctl start cloud-sql-proxy
sudo systemctl status cloud-sql-proxy
```

---

## 🐛 Hibakeresési Tippek

### 1. Access Scope Probléma

**Hibaüzenet:**
```
ERROR 403: Request had insufficient authentication scopes.
Reason: ACCESS_TOKEN_SCOPE_INSUFFICIENT
```

**Megoldás:** VM újraindítása helyes scope-pal (lásd fent)

### 2. IAM Permissions Probléma

**Hibaüzenet:**
```
Insufficient Permission
```

**Megoldás:** Service Account-nak szüksége van `roles/cloudsql.client` role-ra

### 3. Connection String Formátum

**Helyes formátum:**
```
PROJECT_ID:REGION:INSTANCE_NAME
```

**Példa:**
```
newscred-477910:us-central1:newscred
```

### 4. Proxy Nem Indul

**Debug mode indítás:**
```bash
# Ne daemon módban (&), így látod a logokat
/tmp/cloud_sql_proxy --instances=PROJECT_ID:REGION:INSTANCE_NAME=tcp:0.0.0.0:3307
```

**Ellenőrizd:**
- ✅ Helyes connection string?
- ✅ Port szabad (3307)?
- ✅ Service Account jogosultságok?
- ✅ VM access scopes?

---

## 📊 Teljesítmény Összehasonlítás

| Kapcsolódási Módszer | Latency | Biztonság | Kompatibilitás | Ajánlott |
|---------------------|---------|-----------|----------------|----------|
| Direct Private IP   | ~1-2ms  | ⭐⭐⭐     | ⚠️ Plugin függő | ❌       |
| Direct Public IP    | ~2-5ms  | ⭐⭐       | ⚠️ Plugin függő | ❌       |
| Cloud SQL Proxy     | ~2-3ms  | ⭐⭐⭐⭐⭐   | ✅ Minden      | ✅       |

---

## 🎓 Tanulságok

### Mit tanultunk?

1. **"Access Denied" != Rossz jelszó**
   - Autentikációs plugin inkompatibilitás okozhatja
   - CLI és programmatic access különbözően viselkedhet

2. **MySQL 8.0 Breaking Change**
   - `caching_sha2_password` alapértelmezett
   - Régebbi kliensek nem kompatibilisek

3. **Cloud SQL Best Practices**
   - Mindig Cloud SQL Auth Proxy-t használj production-ben
   - IAM-alapú autentikáció > Jelszó-alapú
   - Private IP + Proxy = Legjobb megoldás

4. **GCP VM Scopes Kritikus**
   - Service Account IAM roles ≠ VM Access Scopes
   - Mindkettő szükséges!
   - VM újraindítás szükséges scope váltáshoz

5. **Hibakeresési Módszertan**
   - Reprodukáld különböző kliensekkel (CLI vs. programmatic)
   - Ellenőrizd az autentikációs plugin típusát
   - Debug mode hasznos (proxy logging)

---

## 🔗 Hasznos Linkek

- [Cloud SQL Auth Proxy Documentation](https://cloud.google.com/sql/docs/mysql/sql-proxy)
- [MySQL 8.0 Authentication Plugin](https://dev.mysql.com/doc/refman/8.0/en/caching-sha2-pluggable-authentication.html)
- [PyMySQL Documentation](https://pymysql.readthedocs.io/)
- [GCP VM Service Accounts](https://cloud.google.com/compute/docs/access/service-accounts)

---

## 📝 Összefoglalás

**A probléma gyökere:** MySQL 8.0 `caching_sha2_password` plugin és PyMySQL inkompatibilitás

**Az ajánlott megoldás:** Cloud SQL Auth Proxy használata

**Legfontosabb előnyök:**
- ✅ Teljes kompatibilitás
- ✅ IAM-alapú biztonság
- ✅ Automatikus SSL/TLS
- ✅ Zero kód módosítás az alkalmazásban (csak config)

**Implementációs idő:** ~15-20 perc (VM scope váltással együtt)

---

*Dokumentáció készítve: 2025-11-13*  
*Környezet: GCP Cloud SQL MySQL 8.0 + Python Flask + PyMySQL*  
*Megoldás: Cloud SQL Auth Proxy*
