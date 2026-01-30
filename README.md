# XSUS Sentinel - Twitch View-Botting Detection System

## Opis
Zaawansowany system analityczny do wykrywania view-bottingu na Twitchu. Monitoruje kanały, analizuje zachowanie widzów i archiwizuje czat.

## Stack Technologiczny
*   **Backend:** Python (Flask) + TwitchIO
*   **Frontend:** HTML5 + CSS + Chart.js
*   **Baza Danych:** PostgreSQL
*   **Background Tasks:** Redis + Celery

## Instalacja (Debian) - Automatyczna

1. Pobierz repozytorium:
```bash
cd /opt
git clone <URL_DO_REPOZYTORIUM> xsus_sentinel
cd xsus_sentinel
```

2. Uruchom skrypt instalacyjny (jako root):
```bash
chmod +x setup.sh
./setup.sh
```

3. Postępuj zgodnie z instrukcjami na ekranie. Skrypt poprosi o podanie:
   - Twitch Client ID
   - Twitch Client Secret
   - Twitch IRC Token
   - Discord Webhook (opcjonalnie)

## Instalacja Ręczna (Opcjonalnie)

Jeśli wolisz instalację ręczną, wykonaj poniższe kroki:

### 1. Wymagania Systemowe
*   Debian 10/11/12
*   Python 3.9+
*   PostgreSQL
*   Redis
*   Nginx
*   Git

### 2. Konfiguracja Środowiska
Utwórz wirtualne środowisko i zainstaluj zależności:
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Konfiguracja Aplikacji
Skopiuj plik `.env.example` do `.env` i uzupełnij dane:
```bash
cp .env.example .env
nano .env
chown www-data:www-data .env
chmod 600 .env
```
Upewnij się, że `DATABASE_URL` jest poprawny:
`postgresql://sentry:tytanic232@localhost:5432/xsus_sentinel`

Oraz wprowadź dane Twitch API (Client ID, Secret, IRC Token).

### 4. Inicjalizacja Bazy Danych
Utwórz tabele w bazie danych:
```bash
export FLASK_APP=app
python3 init_db.py
```

### 5. Konfiguracja Systemd
Skopiuj pliki usług do systemd:
```bash
cp systemd/*.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable xsus-web xsus-bot xsus-celery
systemctl start xsus-web xsus-bot xsus-celery
```

### 6. Konfiguracja Nginx
Skopiuj konfigurację Nginx:
```bash
cp nginx/xsus_sentinel.conf /etc/nginx/sites-available/
ln -s /etc/nginx/sites-available/xsus_sentinel.conf /etc/nginx/sites-enabled/
rm /etc/nginx/sites-enabled/default # Opcjonalnie
nginx -t
systemctl restart nginx
```

### 8. Dostęp
Aplikacja dostępna pod adresem: `http://Twitch.xsus.pl` (lub IP serwera).
Hasło dostępu: `tytanic232@`

## Aktualizacja
Aby zaktualizować aplikację, kliknij przycisk "Check for Updates" w panelu dashboard lub wykonaj skrypt aktualizacyjny (jako root):
```bash
cd /opt/xsus_sentinel
./update_code.sh
```

## Struktura Katalogów
*   `app/`: Kod źródłowy aplikacji webowej
*   `bot/`: Kod bota TwitchIO
*   `nginx/`: Konfiguracja serwera WWW
*   `systemd/`: Pliki usług systemowych
