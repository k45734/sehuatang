import os
import sys
import subprocess

# --- [1] 라이브러리 자동 설치 로직 ---
def install_and_import(package):
    try:
        __import__(package.replace('-', '_'))
    except ImportError:
        print(f"📦 {package} 라이브러리가 없습니다. 설치를 시작합니다...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", package])
        print(f"✅ {package} 설치 완료.")

required_packages = ['cloudscraper', 'beautifulsoup4', 'feedgen', 'requests-cache']
for pkg in required_packages:
    install_and_import(pkg)

import re
import time
import sqlite3
import requests_cache, requests
import cloudscraper
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup as bs
from feedgen.feed import FeedGenerator

# --- [2] 설정 구간 ---
TARGET_DOMAIN = "https://www.sehuatang.org"
MY_DIR = os.path.dirname(os.path.realpath(__file__))
RSS_SAVE_PATH = os.path.join(MY_DIR, 'rss.xml')
DB_PATH = os.path.join(MY_DIR, 'sehuatang.db')
BOARD_LIST = ['104', '152', '36' ,'37','2','103']

# HTTP 캐시 설정 (서버 부하 감소 및 속도 향상)
#requests_cache.install_cache(
#    os.path.join(MY_DIR, 'sehuatang_http_cache'), 
#    backend='sqlite', 
#    expire_after=timedelta(hours=1)
#)

def get_working_proxy():
    """일본(JP) 및 미국(US) 국가 코드 프록시 탐색 (free-proxy-list 파싱 + ProxyScrape API 백업) 후 유효성 검사"""
    print("🔍 프록시 탐색을 시작합니다...")
    proxy_candidates = []
    
    # 🔥 프록시 탐색 및 테스트 과정에서는 캐시를 강제로 비활성화
    with requests_cache.disabled():
        # --- [1차 시도] 기존 free-proxy-list.net 웹 크롤링 방식 ---
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
            res = requests.get("https://free-proxy-list.net/", headers=headers, timeout=5)
            
            # 디버깅용 원본 HTML 저장
            with open("proxy_page.html", "w", encoding="utf-8") as f:
                f.write(res.text)
                
            soup = bs(res.text, 'html.parser')
            table = soup.find('table')
            #print(table)

            if table:
                for row in table.select('tbody tr'):
                    cols = row.find_all('td')
                    #print(cols)
                    if len(cols) >= 7:
                        ip = cols[0].text.strip()
                        port = cols[1].text.strip()
                        country_code = cols[2].text.strip().upper() 
                        google_support = cols[5].text.strip().lower() 
                        https_support = cols[6].text.strip().lower()   
                        
                        if ip and port and google_support == 'yes' and https_support == 'yes':# and country_code == 'JP':# 
                            proxy_candidates.append(f"{ip}:{port}")
            #sys.exit(1)
        except Exception as e:
            print(f"⚠️ free-proxy-list 파싱 중 오류 발생: {e}")

        # --- [2차 시도 / 백업] 1차에서 못 찾았을 경우 ProxyScrape API 활용 ---
        if not proxy_candidates:
            print("📌 free-proxy-list에서 프록시를 찾지 못해 안정적인 API(ProxyScrape)로 대체 시도합니다...")
            try:
                countries = ["JP", "US", "SG", "VN", "HK"]  # 원하는 국가 코드 리스트
                country_param = ",".join(countries)

                api_url = f"https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=600000&country={country_param}&ssl=yes"
                print(f"API 요청 주소: {api_url}")
                api_res = requests.get(api_url, timeout=5)
                if api_res.status_code == 200:
                    proxy_candidates = [line.strip() for line in api_res.text.splitlines() if line.strip()]
            except Exception as e:
                print(f"⚠️ ProxyScrape API 호출 중 오류 발생: {e}")

        print(f"📌 조건에 맞는 프록시 후보 총 {len(proxy_candidates)}개 발견. 전체 연결 테스트를 시작합니다.")

        # --- [3차] 전체 후보군 테스트 ---
        for proxy in proxy_candidates:
            proxy = proxy.strip()
            print(proxy)
            if not proxy: 
                continue
            
            proxies = {
                "http": f"http://{proxy}",
                "https": f"http://{proxy}",
            }
            try:
                test_res = requests.get(TARGET_DOMAIN, proxies=proxies, timeout=5)
                
                if test_res.status_code == 200 and "불법·유해정보사이트" not in test_res.text:
                    print(f"✅ 유효한 우회 프록시 연결 성공: {proxy}")
                    return proxies
            except Exception:
                continue
                
    # --- [안전 장치] 유효한 프록시가 전멸했을 때 프로그램 종료 ---
    print("\n❌ 사용 가능한 유효한 프록시를 찾지 못했습니다.")
    print("🚨 타겟 사이트 차단 및 실제 IP 노출 방지를 위해 프로그램을 즉시 종료합니다.")
    sys.exit(1)
	
def init_db():
    """DB 초기화 및 기존 DB 구조 자동 수정(Migration)"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    # 테이블 생성
    cur.execute('''
        CREATE TABLE IF NOT EXISTS torrents (
            magnet TEXT PRIMARY KEY,
            title TEXT,
            link TEXT,
            board_id TEXT,
            reg_date TIMESTAMP
        )
    ''')
    
    # [기존 DB 수정] rss_applied 컬럼 존재 여부 확인 및 추가
    cur.execute("PRAGMA table_info(torrents)")
    columns = [column[1] for column in cur.fetchall()]
    
    if 'rss_applied' not in columns:
        print("🔧 기존 DB 구조를 감지했습니다. rss_applied 컬럼을 추가합니다...")
        try:
            cur.execute('ALTER TABLE torrents ADD COLUMN rss_applied INTEGER DEFAULT 0')
            conn.commit()
            print("✅ DB 업그레이드 완료 (rss_applied 컬럼 추가).")
        except sqlite3.OperationalError:
            pass
            
    return conn

def get_scraper():
    scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows'})
    #proxies = get_working_proxy()
    #if proxies:
    #    scraper.proxies = proxies
    return scraper

def make_rss(items, conn):
    """균형 잡힌 아이템들로 rss.xml 생성 및 DB 상태 업데이트"""
    fg = FeedGenerator()
    fg.title('Sehuatang Smart Balanced RSS')
    fg.link(href=TARGET_DOMAIN, rel='alternate')
    fg.description('신규(🆕) 및 기존(✅) 데이터 통합 업데이트 피드')
    
    applied_magnets = []
    for item in items:
        # item: (0:magnet, 1:title, 2:link, 3:board_id, 4:reg_date, 5:rss_applied)
        fe = fg.add_entry()
        
        # 신규 데이터와 기존 데이터 시각적 구분
        status_icon = "🆕" if item[5] == 0 else "✅"
        display_title = f"{status_icon} [{item[3]}] {item[1]}"
        
        fe.title(display_title)
        fe.link(href=item[0])  # 마그넷 주소
        fe.description(f"Board: {item[3]}<br>Time: {item[4]}<br>Original: <a href='{item[2]}'>Link</a>")
        fe.id(item[0])
        
        applied_magnets.append(item[0])

    fg.rss_file(RSS_SAVE_PATH, pretty=True, encoding='utf-8')
    
    # RSS에 포함된 데이터는 '이미 발행됨(1)' 상태로 변경
    if applied_magnets:
        cur = conn.cursor()
        cur.executemany('UPDATE torrents SET rss_applied = 1 WHERE magnet = ?', [(m,) for m in applied_magnets])
        conn.commit()
    
    print(f"💾 rss.xml 업데이트 완료 (항목 {len(applied_magnets)}개)")

def run_full_crawler():
    conn = init_db()
    scraper = get_scraper()
    
    print(f"🚀 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} 크롤링 시작...")
    
    try:
        first_res = scraper.get(TARGET_DOMAIN)
        sid_match = re.search(r"var safeid\s*=\s*'([^']+)';", first_res.text)
        if sid_match:
            sid = sid_match.group(1)
            for name in ['safeid', '_safe', 'a_confirm', 'age_verified']:
                scraper.cookies.set(name, sid, domain='.sehuatang.org')
    except Exception as e:
        print(f"❌ 사이트 접속 실패: {e}"); return

    new_count = 0
    for board in BOARD_LIST:
        print(f"📂 게시판 {board} 스캔 중...")
        try:
            res = scraper.get(f"{TARGET_DOMAIN}/forum-{board}-1.html")
            soup = bs(res.text, 'html.parser')
            links = soup.select("a.xst")
            
            for link in links[:15]:
                title = link.text.strip()
                detail_url = link.get('href')
                if not detail_url.startswith('http'):
                    detail_url = f"{TARGET_DOMAIN}/{detail_url}"
                
                # 상세 페이지 요청 (캐시 우선 확인)
                d_res = scraper.get(detail_url)
                mag_match = re.search(r'magnet:\?xt=urn:btih:[a-zA-Z0-9]{32,40}', d_res.text, re.I)
                
                if mag_match:
                    magnet = mag_match.group()
                    cur = conn.cursor()
                    # 새 데이터는 rss_applied 가 기본값 0으로 저장됨
                    cur.execute('''
                        INSERT OR IGNORE INTO torrents (magnet, title, link, board_id, reg_date)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (magnet, title, detail_url, board, datetime.now(timezone.utc).isoformat()))
                    if cur.rowcount > 0:
                        new_count += 1
                
                if not getattr(d_res, 'from_cache', False):
                    time.sleep(1.2)
        except: continue

    conn.commit()
    print(f"📊 신규 데이터 추가: {new_count}개")

    # --- [핵심] 게시판별로 신규 데이터 우선, 균형 있게 추출 ---
    balanced_items = []
    cur = conn.cursor()
    for b_id in BOARD_LIST:
        # 1. rss_applied=0(미발행)을 먼저, 2. 그 다음 최신순으로 15개씩 추출
        cur.execute('''
            SELECT * FROM torrents 
            WHERE board_id = ? 
            ORDER BY rss_applied ASC, reg_date DESC 
            LIMIT 15
        ''', (b_id,))
        balanced_items.extend(cur.fetchall())

    # 추출된 전체 데이터를 시간순으로 최종 정렬
    balanced_items.sort(key=lambda x: x[4], reverse=True)

    if balanced_items:
        make_rss(balanced_items, conn)
    
    conn.close()

if __name__ == "__main__":
    run_full_crawler()