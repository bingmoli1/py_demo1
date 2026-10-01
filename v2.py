
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
lock = None


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
                INSERT OR REPLACE INTO SubItem
                (remarks, url, id, convertTarget, sort)
                VALUES (?, ?, ?, ?, ?)
            '''

            cursor.execute(
                insert_or_update_sql,
                (
                    str(id_),
                    url,
                    str(id_),
                    convert_target,
                    id_
                )
            )

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
        print('未提供保留的记录列表，删除了 0 条记录')
        return

    db_path = os.path.join(
        command,
        'guiConfigs',
        'guiNDB.db'
    )

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        placeholders = ', '.join('?' for _ in num_list)

        delete_sql = f'''
            DELETE FROM SubItem
            WHERE sort NOT IN ({placeholders})
        '''

        cursor.execute(delete_sql, num_list)

        conn.commit()

        print(
            f'删除了不在 {num_list} 中的记录，'
            f'共 {cursor.rowcount} 条'
        )

    except sqlite3.Error as e:
        print(f'删除错误: {e}')

    finally:
        conn.close()


class SubGet:

    def __init__(self, browser):
        self.browser = browser
        self.context = None

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
        递归处理 sel_all。

        例如：

        [
            ".btn-default",
            "input"
        ]

        执行过程：

        当前页面
            ↓
        找 .btn-default
            ↓
        获取 .btn-default 的 href
            ↓
        进入 href 页面
            ↓
        找 input
            ↓
        获取 input 的 value / textContent
            ↓
        提取 URL
        """

        if not selectors:
            return []

        el = selectors[0]

        # 等待当前层元素出现
        await page.wait_for_selector(
            el,
            state="attached"
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

            url_pattern = re.compile(
                r'https?://[^\s/$.?#].[^\s]*'
            )

            match_urls = []

            for content in contents:

                if not content:
                    continue

                match = url_pattern.search(content)

                if match:
                    match_urls.append(
                        match.group(0)
                    )

            return match_urls

        # 还有下一层
        else:

            elements = await page.query_selector_all(el)

            all_match_urls = []

            for element in elements:

                # 获取当前元素的 href
                href = await element.get_attribute(
                    'href'
                )

                if not href:
                    continue

                # 转换成完整 URL
                full_href = await page.evaluate(
                    '(href) => new URL(href, location.href).href',
                    href
                )

                new_page = await self.context.new_page()

                try:

                    print(
                        f"进入页面: {full_href}"
                    )

                    await self.goto_with_retry(
                        new_page,
                        full_href
                    )

                    # 继续处理下一层
                    sub_match_urls = await self.scrape_level(
                        new_page,
                        selectors[1:]
                    )

                    all_match_urls.extend(
                        sub_match_urls
                    )

                except Exception as e:

                    print(
                        f"处理 {full_href} 失败: {e}"
                    )

                finally:

                    await new_page.close()

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

        # 同一任务的所有页面共享一个上下文,
        # 保留 Cookie(如 Cloudflare 放行凭证)
        self.context = await self.browser.new_context()

        page = await self.context.new_page()

        try:

            await self.goto_with_retry(
                page,
                url
            )

            # ==================================================
            # sel_all 模式
            # ==================================================
            #
            # 例如：
            #
            # "url": "https://zh.v2nodes.com/",
            # "sel_all": [
            #     ".btn-default",
            #     "input"
            # ]
            #
            # 当前页面：
            #     ↓
            # .btn-default
            #     ↓
            # href
            #     ↓
            # 进入对应页面
            #     ↓
            # input
            #     ↓
            # value
            #     ↓
            # 提取 URL
            #
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

                # 处理所有找到的 URL
                for match_url in match_urls:

                    convert_target = (
                        "mixed"
                        if match_url.endswith(
                            ('.yaml', '.yml')
                        )
                        else ""
                    )

                    # 第一个使用当前 ID
                    if first:

                        num = id_
                        first = False

                    # 后面的创建新 ID
                    else:

                        async with lock:

                            num_add += 1

                            num = (
                                base +
                                num_add
                            )

                    print(
                        f"{id_} - {num}  {match_url}"
                    )

                    # 注意：
                    # 这里不能 return
                    # 否则只会保存第一个 URL
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
            # 普通 sel 模式
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

                # ------------------------------------------
                # 先处理列表页面
                # ------------------------------------------

                if list_el:

                    try:

                        await page.wait_for_selector(
                            list_el,
                            state="attached"
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

                # ------------------------------------------
                # 获取最终 URL
                # ------------------------------------------

                if el:

                    await page.wait_for_selector(
                        el,
                        state="attached"
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

                    url_pattern = re.compile(
                        r'https?://[^\s/$.?#].[^\s]*'
                    )

                    for i, content in enumerate(contents):

                        if not content:
                            continue

                        match = url_pattern.search(
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

                                    num = (
                                        base +
                                        num_add
                                    )

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

            await asyncio.sleep(1)

            await page.close()

            await self.context.close()


async def main():

    global select
    global lock

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

            if not os.path.isfile('init.json'):

                print(
                    '未找到 init.json 文件'
                )

                return

            # ==========================================
            # 读取配置
            # ==========================================

            with open(
                'init.json',
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

            await browser.close()


if __name__ == '__main__':

    asyncio.run(main())
