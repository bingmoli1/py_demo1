
import asyncio
import os
import re
import sys
import json
import sqlite3

import psutil
from playwright.async_api import async_playwright

URL_PATTERN = re.compile(
    r'https?://[^\s/$.?#].[^\s]*'
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0"
)

not_clean_arr = set()
num_add = 0
select = None
lock = None
PAGE_LIMIT = None

DB_PATH = None
DB_CONN = None


def get_running_v2rayn_path():
    for proc in psutil.process_iter(['name', 'exe']):
        try:
            if proc.info['name'] and proc.info['name'] == 'v2rayN.exe':
                exe_path = proc.info['exe']
                return os.path.dirname(exe_path)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None


def get_db_path():
    """
    返回 guiNDB.db 路径。

    路径只扫描一次进程并缓存;没找到时保持 None,
    下次调用重新扫描(v2rayN 可能稍后才启动)。
    """
    global DB_PATH

    if DB_PATH:
        return DB_PATH

    command = get_running_v2rayn_path()

    if not command:
        return None

    DB_PATH = os.path.join(
        command,
        'guiConfigs',
        'guiNDB.db'
    )
    return DB_PATH


def close_db_conn():
    global DB_CONN

    try:
        if DB_CONN is not None:
            DB_CONN.close()
    except sqlite3.Error:
        pass

    DB_CONN = None


def up_sub_item(url, remarks, id_, convert_target):
    if id_ not in not_clean_arr:
        not_clean_arr.add(id_)

    db_path = get_db_path()

    if not db_path:
        print('v2rayN 未运行')
        return

    sql = '''
        INSERT OR REPLACE INTO SubItem
        (remarks, url, id, convertTarget, sort)
        VALUES (?, ?, ?, ?, ?)
    '''
    args = (
        str(id_),
        url,
        str(id_),
        convert_target,
        id_
    )

    # 连接全程复用;失败时重开一次再试
    for attempt in (1, 2):
        try:
            global DB_CONN

            if DB_CONN is None:
                DB_CONN = sqlite3.connect(db_path)

            DB_CONN.execute(sql, args)
            DB_CONN.commit()
            return
        except sqlite3.Error as e:
            print(f"数据库错误: {e}")
            close_db_conn()


def cleanup_database(num_list):
    db_path = get_db_path()

    if not db_path:
        print('v2rayN 未运行')
        return

    if not num_list:
        print('未提供保留的记录列表，删除了 0 条记录')
        return

    placeholders = ', '.join('?' for _ in num_list)

    delete_sql = f'''
        DELETE FROM SubItem
        WHERE sort NOT IN ({placeholders})
    '''

    global DB_CONN

    try:
        if DB_CONN is None:
            DB_CONN = sqlite3.connect(db_path)

        cursor = DB_CONN.execute(delete_sql, num_list)
        DB_CONN.commit()

        print(
            f'删除了不在 {num_list} 中的记录，'
            f'共 {cursor.rowcount} 条'
        )
    except sqlite3.Error as e:
        print(f'删除错误: {e}')


def find_init_json():
    """
    先找当前工作目录,再找 exe/脚本所在目录,
    双击 exe 运行时不受"打开方式"影响。
    """
    paths = [
        os.path.join(os.getcwd(), 'init.json')
    ]

    if getattr(sys, 'frozen', False):
        paths.append(os.path.join(
            os.path.dirname(os.path.abspath(sys.executable)),
            'init.json'
        ))

    paths.append(os.path.join(
        os.path.dirname(os.path.abspath(sys.argv[0])),
        'init.json'
    ))

    for p in paths:
        if os.path.isfile(p):
            return p

    return None


class SubGet:

    def __init__(self, browser):
        self.browser = browser
        self.context = None

    async def make_context(self):
        """
        同一任务共享一个上下文(保留 Cookie)。

        忽略证书错误;拦截图片/字体/媒体请求,
        只抓 DOM 文本,这些资源不需要。
        """
        self.context = await self.browser.new_context(
            user_agent=USER_AGENT,
            ignore_https_errors=True
        )

        async def block_heavy(route):
            if route.request.resource_type in (
                'image', 'font', 'media'
            ):
                await route.abort()
            else:
                await route.continue_()

        await self.context.route('**/*', block_heavy)

    async def goto_with_retry(
        self,
        page,
        url,
        attempts=3,
        timeout=20000
    ):
        last_err = None

        for attempt in range(1, attempts + 1):
            try:
                await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=timeout
                )
                return
            except Exception as e:
                last_err = e
                print(
                    f"加载失败({attempt}/{attempts}): {url}"
                )
                await asyncio.sleep(2)

        raise last_err

    async def scrape_level(self, page, selectors):
        """
        递归处理 sel_all,同层多个链接并发抓取。

        例如:

        [
            ".btn-default",
            "input"
        ]

        执行过程:

        当前页面
            ↓
        找 .btn-default(并发进入所有 href)
            ↓
        找 input
            ↓
        获取 value / textContent
            ↓
        提取 URL
        """

        if not selectors:
            return []

        el = selectors[0]

        await page.wait_for_selector(
            el,
            state="attached",
            timeout=15000
        )

        # 如果已经是最后一层
        if len(selectors) == 1:

            contents = await page.eval_on_selector_all(
                el,
                """
                els => els.map(e =>
                    e.value ||
                    e.textContent ||
                    e.href ||
                    ''
                )
                """
            )

            match_urls = []

            for content in contents:
                if not content:
                    continue

                match = URL_PATTERN.search(content)

                if match:
                    match_urls.append(
                        match.group(0)
                    )

            return match_urls

        # 还有下一层:先收齐本层 href
        elements = await page.query_selector_all(el)

        hrefs = []

        for element in elements:
            href = await element.get_attribute(
                'href'
            )

            if not href:
                continue

            full_href = await page.evaluate(
                '(href) => new URL(href, location.href).href',
                href
            )

            hrefs.append(full_href)

        # 同层并发,总数受 PAGE_LIMIT 限制
        async def child(full_href):
            async with PAGE_LIMIT:
                new_page = await self.context.new_page()
                try:
                    print(
                        f"进入页面: {full_href}"
                    )
                    await self.goto_with_retry(
                        new_page,
                        full_href
                    )
                    return await self.scrape_level(
                        new_page,
                        selectors[1:]
                    )
                except Exception as e:
                    print(
                        f"处理 {full_href} 失败: {e}"
                    )
                    return []
                finally:
                    await new_page.close()

        results = await asyncio.gather(
            *(child(h) for h in hrefs)
        )

        all_match_urls = []

        for sub in results:
            all_match_urls.extend(sub)

        return all_match_urls

    async def initialize(
        self,
        url,
        selectors,
        id_,
        all_levels=False
    ):

        global not_clean_arr
        global num_add
        global select
        global lock

        if id_ not in not_clean_arr:
            not_clean_arr.add(id_)

        # 没有选择器
        if selectors is None:

            convert_target = (
                "mixed"
                if url.endswith(('.yaml', '.yml'))
                else ""
            )

            print(
                f"{id_} - {url}"
            )

            up_sub_item(
                url,
                url,
                id_,
                convert_target
            )

            return

        await self.make_context()

        page = await self.context.new_page()

        try:

            await self.goto_with_retry(
                page,
                url
            )

            # ==================================================
            # sel_all 模式:递归抓取,所有 URL 都保存
            # ==================================================

            if all_levels:

                match_urls = await self.scrape_level(
                    page,
                    selectors
                )

                base = (
                    len(select['select'])
                    if select and 'select' in select
                    else 0
                )

                first = True

                for match_url in match_urls:

                    convert_target = (
                        "mixed"
                        if match_url.endswith(
                            ('.yaml', '.yml')
                        )
                        else ""
                    )

                    if first:
                        num = id_
                        first = False
                    else:
                        async with lock:
                            num_add += 1
                            num = base + num_add

                    print(
                        f"{id_} - {num}  {match_url}"
                    )

                    up_sub_item(
                        match_url,
                        match_url,
                        num,
                        convert_target
                    )

                if not match_urls:
                    print(
                        f"失败：{url}"
                    )

            # ==================================================
            # 普通 sel 模式:只保存第一条
            # ==================================================

            else:

                if (
                    isinstance(selectors, list)
                    and selectors
                ):
                    if len(selectors) > 1:
                        list_el = selectors[0]
                        el = selectors[1]
                    else:
                        list_el = None
                        el = selectors[0]
                else:
                    list_el = None
                    el = (
                        selectors
                        if selectors
                        else None
                    )

                if list_el:
                    try:
                        await page.wait_for_selector(
                            list_el,
                            state="attached",
                            timeout=15000
                        )

                        element = await page.query_selector(
                            list_el
                        )

                        if element:
                            href = await element.get_attribute(
                                'href'
                            )

                            if href:
                                full_href = await page.evaluate(
                                    '(href) => new URL(href, location.href).href',
                                    href
                                )

                                await self.goto_with_retry(
                                    page,
                                    full_href
                                )
                            else:
                                print(
                                    f"选择器 {list_el} 未找到 href 属性"
                                )
                        else:
                            print(
                                f"选择器 {list_el} 未找到元素"
                            )
                    except Exception as e:
                        print(
                            f"处理 {list_el} 时出错: {e}"
                        )

                if el:
                    await page.wait_for_selector(
                        el,
                        state="attached",
                        timeout=15000
                    )

                    contents = await page.eval_on_selector_all(
                        el,
                        """
                        els => els.map(e =>
                            e.textContent ||
                            e.value ||
                            ''
                        )
                        """
                    )

                    for i, content in enumerate(contents):

                        if not content:
                            continue

                        match = URL_PATTERN.search(
                            content
                        )

                        if match:
                            match_url = match.group(0)

                            convert_target = (
                                "mixed"
                                if match_url.endswith(
                                    ('.yaml', '.yml')
                                )
                                else ""
                            )

                            num = id_

                            if i > 0:
                                async with lock:
                                    num_add += 1
                                    base = (
                                        len(select['select'])
                                        if select
                                        and 'select' in select
                                        else 0
                                    )
                                    num = base + num_add

                            print(
                                f"{id_} - {num}  {match_url}"
                            )

                            return up_sub_item(
                                match_url,
                                match_url,
                                num,
                                convert_target
                            )

                    print(
                        f"失败：{url}"
                    )

        finally:
            await page.close()
            await self.context.close()


async def main():

    global select
    global lock
    global PAGE_LIMIT

    executable = (
        "C:\\Program Files (x86)\\Microsoft\\Edge"
        "\\Application\\msedge.exe"
    )

    async with async_playwright() as p:

        browser = await p.chromium.launch(

            headless=True,

            executable_path=executable,

            args=[

                "--disable-gpu",

                "--disable-software-rasterizer",

                "--disable-dev-shm-usage",

                "--disable-extensions",

                "--disable-background-networking",

                "--disable-default-apps",

                "--no-sandbox",

                "--no-first-run",

                "--no-default-browser-check",

                "--disable-sync",

                "--disable-translate",

                "--disable-background-timer-throttling",

                "--disable-renderer-backgrounding",

                "--disable-features=TranslateUI",

                "--blink-settings=imagesEnabled=false",

                "--mute-audio",
            ]
        )

        try:

            # ==========================================
            # 检查 init.json
            # ==========================================

            init_path = find_init_json()

            if not init_path:

                print(
                    '未找到 init.json 文件'
                )

                return

            # ==========================================
            # 读取配置
            # ==========================================

            with open(
                init_path,
                'r',
                encoding='utf-8'
            ) as f:

                select = json.load(f)

            # ==========================================
            # 设置 ID
            # ==========================================

            for i, v in enumerate(
                select['select']
            ):

                v['id'] = i + 1

            # ==========================================
            # 并发控制
            # ==========================================

            sem = asyncio.Semaphore(5)

            lock = asyncio.Lock()

            PAGE_LIMIT = asyncio.Semaphore(10)

            # ==========================================
            # 单个任务
            # ==========================================

            async def task(v, i):

                async with sem:

                    try:

                        if v.get('sel_all'):

                            await SubGet(
                                browser
                            ).initialize(
                                v['url'],
                                v.get('sel_all'),
                                i + 1,
                                all_levels=True
                            )

                        else:

                            await SubGet(
                                browser
                            ).initialize(
                                v['url'],
                                v.get('sel'),
                                i + 1
                            )

                    except Exception as e:

                        print(
                            f"任务 {i + 1} 失败："
                            f"{v['url']}，错误：{e}"
                        )

            # ==========================================
            # 创建任务
            # ==========================================

            tasks = [
                task(v, i)
                for i, v in enumerate(
                    select['select']
                )
            ]

            # ==========================================
            # 等待全部任务完成
            # ==========================================

            await asyncio.gather(
                *tasks
            )

            # ==========================================
            # 清理数据库
            # ==========================================

            cleanup_database(
                sorted(not_clean_arr)
            )

        finally:

            close_db_conn()

            await browser.close()


if __name__ == '__main__':

    asyncio.run(main())
