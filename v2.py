import asyncio
from playwright.async_api import async_playwright
import psutil
import os
import sqlite3
import json
import re

not_clean_arr = set()
num_add = 0
select = None


def get_running_v2rayn_path():
    for proc in psutil.process_iter(['name', 'exe']):
        try:
            if proc.info['name'] and proc.info['name'] == 'v2rayN.exe':
                exe_path = proc.info['exe']
                return os.path.dirname(exe_path)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None


def up_sub_item(url, remarks, id_, convert_target):
    if id_ not in not_clean_arr:
        not_clean_arr.add(id_)
    command = get_running_v2rayn_path()
    if command:
        db_path = os.path.join(command, 'guiConfigs', 'guiNDB.db')
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            insert_or_update_sql = '''
                INSERT OR REPLACE INTO SubItem (remarks, url, id, convertTarget, sort)
                VALUES (?, ?, ?, ?, ?)
            '''
            cursor.execute(insert_or_update_sql, (str(id_), url, str(id_), convert_target, id_))
            conn.commit()
        except sqlite3.Error as e:
            print(f"数据库错误: {e}")
        finally:
            conn.close()
    else:
        print('v2rayN 未运行')


def cleanup_database(num_list):
    command = get_running_v2rayn_path()
    if not command:
        print('v2rayN 未运行')
        return
    if not num_list:
        print(f'未提供保留的记录列表，删除了 0 条记录')
        return
    db_path = os.path.join(command, 'guiConfigs', 'guiNDB.db')
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        placeholders = ', '.join('?' for _ in num_list)
        delete_sql = f'DELETE FROM SubItem WHERE sort NOT IN ({placeholders})'
        cursor.execute(delete_sql, num_list)
        conn.commit()
        print(f'删除了不在 {num_list} 中的记录，共 {cursor.rowcount} 条')
    except sqlite3.Error as e:
        print(f'删除错误: {e}')
    finally:
        conn.close()


class SubGet:
    def __init__(self, browser):
        self.browser = browser

    async def scrape_level(self, page, selectors):
        if not selectors:
            return []
        el = selectors[0]
        await page.wait_for_selector(el,state="attached")
        if len(selectors) == 1:
            contents = await page.eval_on_selector_all(el, f"els => els.map(e => e.textContent  ||  e.value || e.getAttribute('{el}')|| '')")
            url_pattern = re.compile(r'https?://[^\s/$.?#].[^\s]*')
            match_urls = []
            for content in contents:
                if content:
                    match = url_pattern.search(content)
                    if match:
                        match_urls.append(match.group(0))
            return match_urls
        else:
            elements = await page.query_selector_all(el)
            all_match_urls = []
            for element in elements:
                href = await element.get_attribute('href')
                if href:
                    full_href = await page.evaluate('(href) => new URL(href, location.href).href', href)
                    new_page = await self.browser.new_page()
                    try:
                        await new_page.goto(full_href, wait_until="domcontentloaded")
                        sub_match_urls = await self.scrape_level(new_page, selectors[1:])
                        all_match_urls.extend(sub_match_urls)
                    except Exception as e:
                        print(f"处理 {full_href} 失败: {e}")
                    finally:
                        await new_page.close()
            return all_match_urls

    async def initialize(self, url, selectors, id_, all_levels=False):
        global not_clean_arr, num_add, select
        if id_ not in not_clean_arr:
            not_clean_arr.add(id_)
        if selectors is None:
            convert_target = "mixed" if url.endswith(('.yaml', '.yml')) else ""
            print(f"{id_} - {url}")
            up_sub_item(url, url, id_, convert_target)
            return

        page = await self.browser.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded")
            if all_levels:
                match_urls = await self.scrape_level(page, selectors)
                base = len(select['select']) if select and 'select' in select else 0
                first = True
                for match_url in match_urls:
                    convert_target = "mixed" if match_url.endswith(('.yaml', '.yml')) else ""
                    if first:
                        num = id_
                        first = False
                    else:
                        async with lock:
                            num_add += 1
                            num = base + num_add
                    print(f"{id_} -  {num}  {match_url}")
                    up_sub_item(match_url, match_url, num, convert_target)
            else:
                if isinstance(selectors, list) and selectors:
                    if len(selectors) > 1:
                        list_el, el = selectors[0], selectors[1]
                    else:
                        list_el, el = None, selectors[0]
                else:
                    list_el = None
                    el = selectors if selectors else None

                if list_el:
                    try:
                        await page.wait_for_selector(list_el, state="attached")
                        element = await page.query_selector(list_el)
                        if element:
                            href = await element.get_attribute('href')
                            if href:
                                full_href = await page.evaluate('(href) => new URL(href, location.href).href', href)
                                await page.goto(full_href, wait_until="domcontentloaded")

                            else:
                                print(f"选择器 {list_el} 未找到 href 属性")
                        else:
                            print(f"选择器 {list_el} 未找到元素")
                    except Exception as e:
                        print(f"处理 {list_el} 时出错: {e}")

                if el:
                    await page.wait_for_selector(el, state="attached")
                    contents = await page.eval_on_selector_all(el, "els => els.map(e => e.textContent || e.value)")
                    url_pattern = re.compile(r'https?://[^\s/$.?#].[^\s]*')
                    for i, content in enumerate(contents):
                        if not content:
                            continue
                        match = url_pattern.search(content)
                        if match:
                            match_url = match.group(0)
                            convert_target = "mixed" if match_url.endswith(('.yaml', '.yml')) else ""
                            num = id_
                            if i > 0:
                                async with lock:
                                    num_add += 1
                                    base = len(select['select']) if select and 'select' in select else 0
                                    num = base + num_add
                            print(f"{id_} - {num}  {match_url}")
                            up_sub_item(match_url, match_url, num, convert_target)
        finally:
            await asyncio.sleep(1)
            await page.close()


async def main():
    global select, lock
    executable = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            executable_path=executable,
            args=[
                "--disable-gpu",  # 禁用 GPU，加快启动，省内存
                "--disable-software-rasterizer",  # 禁用软件光栅化
                "--disable-dev-shm-usage",  # 避免 /dev/shm 限制，防止内存占用爆掉
                "--disable-extensions",  # 禁用扩展
                "--disable-background-networking",  # 禁用后台网络活动
                "--disable-default-apps",  # 禁用默认应用
                "--no-sandbox",  # 去掉沙盒（不安全，但更快，CI 常用）
                "--no-first-run",  # 跳过首次运行检查
                "--no-default-browser-check",  # 不检查默认浏览器
                "--disable-sync",  # 禁用同步服务
                "--disable-translate",  # 禁用翻译
                "--disable-background-timer-throttling",  # 禁用后台定时器节流
                "--disable-renderer-backgrounding",  # 禁用渲染器后台化
                "--disable-features=TranslateUI",  # 禁用翻译 UI
                "--blink-settings=imagesEnabled=false",  # 禁用图片（你已有）
                "--mute-audio",  # 静音，避免音频加载
            ]
        )

        try:
            if not os.path.isfile('init.json'):
                print('未找到 init.json 文件')
                return

            with open('init.json', 'r', encoding='utf-8') as f:
                select = json.load(f)
            for i, v in enumerate(select['select']):
                v['id'] = i + 1

            sem = asyncio.Semaphore(5)
            lock = asyncio.Lock()

            async def task(v, i):
                async with sem:
                    try:
                        if v.get('sel_all'):
                            await SubGet(browser).initialize(v['url'], v.get('sel_all'), i + 1, all_levels=True)
                        else:
                            await SubGet(browser).initialize(v['url'], v.get('sel'), i + 1)
                    except Exception as e:
                        print(f"任务 {i + 1} 失败: {v['url']}，错误: {e}")

            tasks = [task(v, i) for i, v in enumerate(select['select'])]
            await asyncio.gather(*tasks)

            cleanup_database(sorted(not_clean_arr))
        finally:
            await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
