# spider_scraper.py
# Python 3.10+  |  pip install requests beautifulsoup4 lxml pillow
from __future__ import annotations

import os
import sys
import time
import threading
import queue
import urllib.parse as urlparse
import urllib.robotparser
import webbrowser
import tempfile
import random
import re
import json
from dataclasses import dataclass, asdict
from typing import List, Set, Dict, Tuple, Any, Optional


import requests
from bs4 import BeautifulSoup

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

try:
    # Optional screenshot support (Windows/macOS/X11 with permissions)
    from PIL import ImageGrab
except Exception:
    ImageGrab = None


APP_TITLE = "Spider Scraper"
USER_AGENT = "RetroPhantomSpider/1.0 (+https://example.com)"
REQUEST_TIMEOUT = 12
CRAWL_DELAY = 0.3
MAX_PAGES = 150
ALLOWED_SCHEMES = {"http", "https"}

# Common User-Agents for randomization
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

@dataclass
class Row:
    title: str
    category: str
    url: str

def log_safe(text_widget: ScrolledText, msg: str) -> None:
    text_widget.configure(state="normal")
    text_widget.insert("end", msg.rstrip() + "\n")
    text_widget.see("end")
    text_widget.configure(state="disabled")

def same_origin(a: str, b: str) -> bool:
    try:
        pa = urlparse.urlparse(a)
        pb = urlparse.urlparse(b)
        return (pa.scheme, pa.netloc) == (pb.scheme, pb.netloc)
    except Exception:
        return False

def normalize_link(base_url: str, href: str) -> str | None:
    if not href:
        return None
    href = href.strip()
    # ignore anchors and javascript/mailto/tel
    if href.startswith("#") or ":" in href.split("?")[0] and href.split(":")[0] not in ("http", "https"):
        if href.startswith("http") or href.startswith("https"):
            pass  # allow absolute http(s)
        else:
            # Allow relative links, "javascript:" etc. are dropped below
            pass
    # Build absolute
    abs_url = urlparse.urljoin(base_url, href)
    parsed = urlparse.urlparse(abs_url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        return None
    # Strip fragment
    abs_url = abs_url.split("#", 1)[0]
    return abs_url

class Spider:
    """Advanced crawler with Phase 1, 2, and 3 features."""
    def __init__(self, root_url: str, max_pages: int = MAX_PAGES, delay: float = CRAWL_DELAY,
                 user_agent: Optional[str] = None, randomize_ua: bool = False,
                 respect_robots: bool = True, custom_headers: Optional[Dict[str, str]] = None,
                 cookies: Optional[Dict[str, str]] = None, max_depth: int = -1,
                 allowed_domains: Optional[List[str]] = None, link_filters: Optional[List[str]] = None,
                 max_retries: int = 3, retry_backoff: float = 1.0,
                 proxies: Optional[List[str]] = None, randomize_delay: bool = False,
                 delay_min: Optional[float] = None, delay_max: Optional[float] = None,
                 include_keywords: Optional[List[str]] = None, exclude_keywords: Optional[List[str]] = None):
        self.root = root_url.rstrip("/")
        self.max_pages = max_pages
        self.delay = delay
        self.max_depth = max_depth  # -1 means unlimited
        self.allowed_domains = allowed_domains or []
        self.link_filters = link_filters or []  # e.g., ['.html', '.htm']
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.randomize_ua = randomize_ua
        self.respect_robots = respect_robots
        
        # Phase 3: Proxy rotation
        self.proxies = proxies or []
        self.current_proxy_index = 0
        
        # Phase 3: Rate control with randomization
        self.randomize_delay = randomize_delay
        self.delay_min = delay_min if delay_min is not None else delay * 0.5
        self.delay_max = delay_max if delay_max is not None else delay * 1.5
        
        # Phase 3: Keyword filters
        self.include_keywords = include_keywords or []
        self.exclude_keywords = exclude_keywords or []
        
        # Setup session
        self.session = requests.Session()
        
        # User-Agent handling
        if randomize_ua:
            self.user_agent = random.choice(USER_AGENTS)
        elif user_agent:
            self.user_agent = user_agent
        else:
            self.user_agent = USER_AGENT
        self.session.headers.update({"User-Agent": self.user_agent})
        
        # Custom headers
        if custom_headers:
            self.session.headers.update(custom_headers)
        
        # Cookies
        if cookies:
            self.session.cookies.update(cookies)
        
        # Phase 3: Set initial proxy if available
        if self.proxies:
            self._set_proxy()
        
        # Robots.txt parser cache
        self.robots_parsers: Dict[str, urllib.robotparser.RobotFileParser] = {}
        
        # State
        self.seen: Set[str] = set()
        self.rows: List[Row] = []
        self.cancelled = False
        self.url_depths: Dict[str, int] = {}  # Track depth for each URL

    def cancel(self):
        self.cancelled = True
    
    def _set_proxy(self):
        """Set the next proxy from the rotation list"""
        if not self.proxies:
            return
        
        proxy = self.proxies[self.current_proxy_index]
        # Parse proxy format (http://host:port or https://host:port)
        if not proxy.startswith(('http://', 'https://')):
            proxy = f"http://{proxy}"
        
        self.session.proxies = {
            'http': proxy,
            'https': proxy
        }
        
        # Rotate to next proxy
        self.current_proxy_index = (self.current_proxy_index + 1) % len(self.proxies)
    
    def _get_delay(self) -> float:
        """Get delay with optional randomization"""
        if self.randomize_delay:
            return random.uniform(self.delay_min, self.delay_max)
        return self.delay
    
    def _check_keyword_filters(self, url: str) -> bool:
        """Check if URL matches keyword filters"""
        url_lower = url.lower()
        
        # Check exclude keywords first
        for keyword in self.exclude_keywords:
            if keyword.lower() in url_lower:
                return False
        
        # Check include keywords
        if self.include_keywords:
            for keyword in self.include_keywords:
                if keyword.lower() in url_lower:
                    return True
            return False  # If include keywords specified but none match
        
        return True  # No filters or passed all checks
    
    def _get_robots_parser(self, url: str) -> Optional[urllib.robotparser.RobotFileParser]:
        """Get or create robots.txt parser for a domain."""
        if not self.respect_robots:
            return None
        
        parsed = urlparse.urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        
        if robots_url not in self.robots_parsers:
            rp = urllib.robotparser.RobotFileParser()
            rp.set_url(robots_url)
            try:
                rp.read()
                self.robots_parsers[robots_url] = rp
            except Exception:
                # If robots.txt can't be read, allow crawling
                return None
        
        return self.robots_parsers.get(robots_url)
    
    def _can_fetch(self, url: str) -> bool:
        """Check if URL can be fetched according to robots.txt."""
        if not self.respect_robots:
            return True
        
        rp = self._get_robots_parser(url)
        if rp is None:
            return True
        
        return rp.can_fetch(self.user_agent, url)
    
    def _fetch_with_retry(self, url: str, log_cb=None) -> Optional[requests.Response]:
        """Fetch URL with retry logic and exponential backoff."""
        for attempt in range(self.max_retries + 1):
            try:
                if self.randomize_ua and attempt > 0:
                    # Randomize UA on retry
                    self.session.headers.update({"User-Agent": random.choice(USER_AGENTS)})
                
                if log_cb and attempt > 0:
                    log_cb(f"  Retry {attempt}/{self.max_retries} for {url}")
                
                r = self.session.get(url, timeout=REQUEST_TIMEOUT)
                r.raise_for_status()
                return r
            except Exception as e:
                if attempt < self.max_retries:
                    wait_time = self.retry_backoff * (2 ** attempt)
                    if log_cb:
                        log_cb(f"  ! {e}, retrying in {wait_time:.1f}s...")
                    time.sleep(wait_time)
                else:
                    if log_cb:
                        log_cb(f"  ! Failed after {self.max_retries} retries: {e}")
                    return None
        return None
    
    def _check_domain_allowed(self, url: str) -> bool:
        """Check if URL domain is in allowed domains list."""
        if not self.allowed_domains:
            return True
        
        parsed = urlparse.urlparse(url)
        domain = parsed.netloc.lower()
        
        for allowed in self.allowed_domains:
            allowed = allowed.lower().strip()
            if allowed.startswith('.'):
                # Subdomain matching: .example.com matches *.example.com
                if domain.endswith(allowed) or domain == allowed[1:]:
                    return True
            else:
                # Exact or subdomain matching
                if domain == allowed or domain.endswith('.' + allowed):
                    return True
        
        return False
    
    def _check_link_filter(self, url: str) -> bool:
        """Check if URL matches link filters."""
        if not self.link_filters:
            return True
        
        parsed = urlparse.urlparse(url)
        path = parsed.path.lower()
        
        for filter_ext in self.link_filters:
            filter_ext = filter_ext.lower().strip()
            if not filter_ext.startswith('.'):
                filter_ext = '.' + filter_ext
            if path.endswith(filter_ext) or path.endswith(filter_ext + '/'):
                return True
        
        return False
    
    def _get_url_depth(self, url: str) -> int:
        """Get depth of URL from root."""
        if url in self.url_depths:
            return self.url_depths[url]
        
        if url == self.root:
            return 0
        
        # Calculate depth based on path segments
        root_parsed = urlparse.urlparse(self.root)
        url_parsed = urlparse.urlparse(url)
        
        if root_parsed.netloc != url_parsed.netloc:
            return -1  # Different domain
        
        root_path = root_parsed.path.rstrip('/').split('/')
        url_path = url_parsed.path.rstrip('/').split('/')
        
        # Count additional path segments
        depth = max(0, len(url_path) - len(root_path))
        self.url_depths[url] = depth
        return depth

    def crawl(self, progress_cb=None, log_cb=None):
        q: queue.Queue[Tuple[str, int]] = queue.Queue()  # (url, depth)
        q.put((self.root, 0))
        self.seen.add(self.root)
        self.url_depths[self.root] = 0

        while not q.empty() and len(self.rows) < self.max_pages and not self.cancelled:
            url, depth = q.get()
            
            # Check depth limit
            if self.max_depth >= 0 and depth > self.max_depth:
                if log_cb:
                    log_cb(f"  Skipping {url} (depth {depth} > max {self.max_depth})")
                continue
            
            # Check robots.txt
            if not self._can_fetch(url):
                if log_cb:
                    log_cb(f"  Blocked by robots.txt: {url}")
                continue
            
            # Check domain restrictions
            if not self._check_domain_allowed(url):
                if log_cb:
                    log_cb(f"  Domain not allowed: {url}")
                continue
            
            # Check link filters
            if not self._check_link_filter(url):
                if log_cb:
                    log_cb(f"  Link filter mismatch: {url}")
                continue
            
            # Phase 3: Check keyword filters
            if not self._check_keyword_filters(url):
                if log_cb:
                    log_cb(f"  Keyword filter mismatch: {url}")
                continue
            
            # Phase 3: Rotate proxy if enabled
            if self.proxies:
                self._set_proxy()
            
            # Fetch with retry
            if log_cb:
                log_cb(f"GET {url} (depth {depth})")
            
            r = self._fetch_with_retry(url, log_cb)
            if r is None:
                continue
            
            html = r.text
            soup = BeautifulSoup(html, "lxml")

            # Title
            title = (soup.title.string.strip() if soup.title and soup.title.string else url)

            # "Category" heuristic: first path segment
            path = urlparse.urlparse(url).path.strip("/")
            category = path.split("/")[0] if path else ""

            self.rows.append(Row(title=title, category=category, url=url))
            if progress_cb:
                progress_cb(title, html)

            # Queue more links
            for a in soup.select("a[href]"):
                abs_url = normalize_link(url, a.get("href"))
                if not abs_url:
                    continue
                if not same_origin(self.root, abs_url):
                    continue
                if abs_url in self.seen:
                    continue
                
                # Check depth for new URL
                new_depth = self._get_url_depth(abs_url)
                if self.max_depth >= 0 and new_depth > self.max_depth:
                    continue
                
                # Phase 3: Check keyword filters for new URLs
                if not self._check_keyword_filters(abs_url):
                    continue
                
                self.seen.add(abs_url)
                q.put((abs_url, new_depth))

            # Phase 3: Delay with optional randomization
            delay = self._get_delay()
            time.sleep(delay)

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("980x600")
        self.minsize(900, 560)

        # State
        self.spider_thread: threading.Thread | None = None
        self.spider: Spider | None = None
        self.rows: List[Row] = []
        self.queue_preview: queue.Queue[Tuple[str, str]] = queue.Queue()
        self.sort_column: str | None = None
        self.sort_reverse: bool = False
        self.current_html: str = ""
        self.current_url: str = ""
        self.scraped_images: List[Dict[str, Any]] = []  # Store scraped images: {url, data, filename, checkbox_var}
        self.image_checkboxes: List[tk.BooleanVar] = []  # Store checkbox variables for image selection
        
        # UI State
        self.filter_text: str = ""
        self.tree_item_ids: Dict[str, str] = {}  # Map row index to tree item ID for highlighting
        
        # Phase 1 Settings
        self.settings = {
            'user_agent': USER_AGENT,
            'randomize_ua': False,
            'respect_robots': True,
            'custom_headers': {},
            'cookies': {},
            'max_depth': -1,  # -1 means unlimited
            'allowed_domains': [],
            'link_filters': [],
            'max_retries': 3,
            'retry_backoff': 1.0,
            'randomize_delay': False,
        }
        
        # Phase 2 Settings
        self.screenshot_per_page = False
        self.screenshot_dir: Optional[str] = None
        
        # Phase 3 Settings
        self.settings['proxies'] = []
        self.settings['delay_min'] = None
        self.settings['delay_max'] = None
        self.settings['include_keywords'] = []
        self.settings['exclude_keywords'] = []
        
        self._build_ui()
        self.after(75, self._drain_preview_queue)

    # ---------- UI ----------
    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        # Top bar: Root + actions (compact padding)
        top = ttk.LabelFrame(self, text=" Input Section", padding=(8, 4))
        top.grid(row=0, column=0, sticky="ew", padx=5, pady=3)
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="Root:").grid(row=0, column=0, padx=(0,6))
        self.var_root = tk.StringVar(value="https://websitename.com/")
        self.ent_root = ttk.Entry(top, textvariable=self.var_root)
        self.ent_root.grid(row=0, column=1, sticky="ew")

        ttk.Button(top, text="Create Sitemap", command=self.on_create_sitemap).grid(row=0, column=2, padx=4)
        ttk.Button(top, text="Save Sitemap", command=self.on_save_sitemap).grid(row=0, column=3, padx=4)
        ttk.Button(top, text="Load Sitemap", command=self.on_load_sitemap).grid(row=0, column=4, padx=4)
        ttk.Button(top, text="Settings", command=self.on_settings).grid(row=0, column=5, padx=4)

        # Main split: Use PanedWindow for resizable panes
        main_paned = ttk.PanedWindow(self, orient="horizontal")
        main_paned.grid(row=2, column=0, sticky="nsew", padx=5, pady=2)
        
        # Left pane: Results Table
        left_frame = ttk.LabelFrame(main_paned, text=" Results Table", padding=(5, 3))
        left_frame.columnconfigure(0, weight=1)
        left_frame.rowconfigure(1, weight=1)
        
        # Filter box
        filter_frame = ttk.Frame(left_frame)
        filter_frame.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        filter_frame.columnconfigure(1, weight=1)
        
        ttk.Label(filter_frame, text="🔍 Filter:").grid(row=0, column=0, padx=(0, 5))
        self.var_filter = tk.StringVar(value="")
        self.var_filter.trace("w", lambda *args: self.on_filter_changed())
        self.ent_filter = ttk.Entry(filter_frame, textvariable=self.var_filter)
        self.ent_filter.grid(row=0, column=1, sticky="ew")
        ttk.Button(filter_frame, text="Clear", command=lambda: self.var_filter.set("")).grid(row=0, column=2, padx=(5, 0))
        
        # Treeview (Title, Category, URL) - enable multi-select
        tree_frame = ttk.Frame(left_frame)
        tree_frame.grid(row=1, column=0, sticky="nsew")
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)
        
        self.tree = ttk.Treeview(tree_frame, columns=("title", "category", "url"), show="headings", height=12, selectmode="extended")
        self.tree.heading("title", text="Title", command=lambda: self.on_column_click("title"))
        self.tree.heading("category", text="Category", command=lambda: self.on_column_click("category"))
        self.tree.heading("url", text="URL", command=lambda: self.on_column_click("url"))
        self.tree.column("title", width=180, anchor="w")
        self.tree.column("category", width=110, anchor="w")
        self.tree.column("url", width=260, anchor="w")
        
        # Configure alternating row colors - text always black for legibility
        self.tree.tag_configure("evenrow", background="#f0f0f0", foreground="black")
        self.tree.tag_configure("oddrow", background="white", foreground="black")
        self.tree.tag_configure("highlight", background="#ffff99", foreground="black")  # Yellow for flash
        
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree.bind("<<TreeviewSelect>>", self.on_row_selected)
        self.tree.bind("<Button-3>", self.on_right_click)  # Right-click context menu
        self.tree.bind("<Double-1>", self.on_tree_double_click)  # Double-click to open URL

        tv_scroll_y = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tv_scroll_y.set)
        tv_scroll_y.grid(row=0, column=1, sticky="ns")
        
        main_paned.add(left_frame, weight=2)
        
        # Right pane: Page Preview
        right_frame = ttk.LabelFrame(main_paned, text=" Page Preview", padding=(5, 3))
        right_frame.columnconfigure(0, weight=1)
        right_frame.rowconfigure(0, weight=1)

        # Right: header
        self.var_page_title = tk.StringVar(value="Page Selected Title")
        hdr = ttk.Label(right_frame, textvariable=self.var_page_title, anchor="center", style="Header.TLabel")
        hdr.grid(row=0, column=0, sticky="ew", pady=(0,2))
        s = ttk.Style(self)
        s.configure("Header.TLabel", font=("Segoe UI", 10, "bold"))

        # Notebook with multiple tabs
        self.nb = ttk.Notebook(right_frame)
        self.nb.grid(row=1, column=0, sticky="nsew")
        right_frame.rowconfigure(1, weight=1)
        
        main_paned.add(right_frame, weight=3)
        
        self.txt_text = ScrolledText(self.nb, wrap="word")
        self.txt_xml = ScrolledText(self.nb, wrap="word")
        self.txt_html = ScrolledText(self.nb, wrap="word")
        self.txt_css = ScrolledText(self.nb, wrap="word")
        self.txt_js = ScrolledText(self.nb, wrap="word")
        self.txt_metadata = ScrolledText(self.nb, wrap="word")
        self.txt_tables = ScrolledText(self.nb, wrap="word")
        self.txt_json = ScrolledText(self.nb, wrap="word", state="disabled")
        self.txt_json.bind("<Button-3>", self.on_json_right_click)
        
        # Regex Extraction Panel
        regex_frame = ttk.Frame(self.nb)
        regex_top = ttk.Frame(regex_frame)
        regex_top.pack(fill="x", padx=5, pady=5)
        ttk.Label(regex_top, text="Regex Pattern:").pack(side="left", padx=5)
        self.var_regex_pattern = tk.StringVar(value="")
        self.ent_regex = ttk.Entry(regex_top, textvariable=self.var_regex_pattern, width=40)
        self.ent_regex.pack(side="left", padx=5, fill="x", expand=True)
        self.ent_regex.bind("<Button-3>", self.on_regex_entry_right_click)
        ttk.Button(regex_top, text="Search", command=self.on_regex_search).pack(side="left", padx=5)
        ttk.Button(regex_top, text="Clear", command=self.on_regex_clear).pack(side="left", padx=5)
        self.txt_regex = ScrolledText(regex_frame, wrap="word")
        self.txt_regex.configure(font=("Consolas", 10))
        self.txt_regex.pack(fill="both", expand=True, padx=5, pady=5)
        
        for w in (self.txt_text, self.txt_xml, self.txt_html, self.txt_css, self.txt_js, 
                  self.txt_metadata, self.txt_tables):
            w.configure(font=("Consolas", 10))
            w.bind("<Button-3>", self.on_text_preview_right_click)
        
        # JSON is handled separately (read-only with copy)
        self.txt_json.configure(font=("Consolas", 10))
        
        # Scraped Images tab
        self.frame_images = ttk.Frame(self.nb)
        images_canvas_frame = ttk.Frame(self.frame_images)
        images_canvas_frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Canvas with scrollbar for images
        self.images_canvas = tk.Canvas(images_canvas_frame)
        images_scrollbar = ttk.Scrollbar(images_canvas_frame, orient="vertical", command=self.images_canvas.yview)
        self.images_container = ttk.Frame(self.images_canvas)
        
        self.images_canvas.configure(yscrollcommand=images_scrollbar.set)
        self.images_canvas.pack(side="left", fill="both", expand=True)
        images_scrollbar.pack(side="right", fill="y")
        
        self.images_canvas_window = self.images_canvas.create_window((0, 0), window=self.images_container, anchor="nw")
        
        def configure_scroll_region(event):
            self.images_canvas.configure(scrollregion=self.images_canvas.bbox("all"))
            self.images_canvas.itemconfig(self.images_canvas_window, width=self.images_canvas.winfo_width())
        
        self.images_container.bind("<Configure>", configure_scroll_region)
        self.images_canvas.bind("<Configure>", lambda e: self.images_canvas.itemconfig(self.images_canvas_window, width=e.width))
        
        # Buttons for saving images
        images_btn_frame = ttk.Frame(self.frame_images)
        images_btn_frame.pack(fill="x", padx=5, pady=5)
        ttk.Button(images_btn_frame, text="Save All Images", command=self.on_save_all_images).pack(side="left", padx=5)
        ttk.Button(images_btn_frame, text="Save Selected Images", command=self.on_save_selected_images).pack(side="left", padx=5)
        self.var_images_count = tk.StringVar(value="No images scraped")
        ttk.Label(images_btn_frame, textvariable=self.var_images_count).pack(side="left", padx=10)

        self.nb.add(self.txt_text, text="Text View")
        self.nb.add(self.txt_xml, text="XML View")
        self.nb.add(self.txt_html, text="HTML Preview")
        self.nb.add(self.txt_css, text="CSS Preview")
        self.nb.add(self.txt_js, text="JavaScript Preview")
        self.nb.add(self.txt_metadata, text="Metadata")
        self.nb.add(self.txt_tables, text="Tables")
        self.nb.add(self.txt_json, text="JSON")
        self.nb.add(regex_frame, text="Regex Search")
        self.nb.add(self.frame_images, text="Scraped Images")

        # Buttons under notebook (compact)
        btns = ttk.Frame(self, padding=(5, 3))
        btns.grid(row=3, column=0, sticky="ew", padx=5, pady=2)
        btns.columnconfigure(10, weight=1)
        ttk.Button(btns, text="Export XML", command=self.on_export_xml).grid(row=0, column=0, padx=3)
        ttk.Button(btns, text="Export HTML", command=self.on_export_html).grid(row=0, column=1, padx=3)
        ttk.Button(btns, text="Save Text", command=self.on_save_tab_text).grid(row=0, column=2, padx=3)
        ttk.Button(btns, text="Open in Browser", command=self.on_open_in_browser).grid(row=0, column=3, padx=3)
        ttk.Button(btns, text="Clear", command=self.on_clear).grid(row=0, column=4, padx=3)
        ttk.Button(btns, text="Scrape Images", command=self.on_scrape_images).grid(row=0, column=5, padx=3)
        ttk.Button(btns, text="Screenshot", command=self.on_screenshot).grid(row=0, column=6, padx=3)

        # Log area (in group box)
        log_frame = ttk.LabelFrame(self, text=" Logs", padding=(5, 3))
        log_frame.grid(row=4, column=0, sticky="nsew", padx=5, pady=2)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log = ScrolledText(log_frame, height=6, state="disabled")
        self.log.configure(font=("Consolas", 9))
        self.log.grid(row=0, column=0, sticky="nsew")

        # Progress bar frame at bottom
        progress_frame = ttk.Frame(self, padding=(10, 6, 10, 6))
        progress_frame.grid(row=5, column=0, sticky="ew")
        progress_frame.columnconfigure(1, weight=1)
        
        self.var_pages_scraped = tk.StringVar(value="Pages scraped: 0")
        ttk.Label(progress_frame, textvariable=self.var_pages_scraped, anchor="w").grid(row=0, column=0, sticky="w", padx=(0, 10))
        
        self.progress_bar = ttk.Progressbar(progress_frame, mode="determinate", maximum=MAX_PAGES)
        self.progress_bar.grid(row=0, column=1, sticky="ew", padx=(0, 10))
        
        # status bar
        self.var_status = tk.StringVar(value="Ready")
        status = ttk.Label(self, textvariable=self.var_status, anchor="w", relief="sunken")
        status.grid(row=6, column=0, sticky="ew")

    # ---------- Actions ----------
    def on_create_sitemap(self):
        root = self.var_root.get().strip()
        if not root:
            messagebox.showwarning(APP_TITLE, "Enter a root URL.")
            return
        try:
            p = urlparse.urlparse(root)
            if p.scheme not in ALLOWED_SCHEMES or not p.netloc:
                raise ValueError
        except Exception:
            messagebox.showerror(APP_TITLE, "Invalid URL. Example: https://example.com")
            return

        # reset
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self.rows.clear()
        self.tree_item_ids.clear()
        
        # Reset progress bar
        self.var_pages_scraped.set("Pages scraped: 0")
        self.progress_bar['value'] = 0
        self.var_filter.set("")  # Clear filter
        
        # Clear previews
        self.txt_text.delete("1.0", "end")
        self.txt_xml.delete("1.0", "end")
        self.txt_html.delete("1.0", "end")
        self.txt_css.delete("1.0", "end")
        self.txt_js.delete("1.0", "end")

        # Create Spider with Phase 1, 2, and 3 settings
        self.spider = Spider(
            root,
            max_pages=MAX_PAGES,
            delay=CRAWL_DELAY,
            user_agent=self.settings['user_agent'] if not self.settings['randomize_ua'] else None,
            randomize_ua=self.settings['randomize_ua'],
            respect_robots=self.settings['respect_robots'],
            custom_headers=self.settings['custom_headers'] if self.settings['custom_headers'] else None,
            cookies=self.settings['cookies'] if self.settings['cookies'] else None,
            max_depth=self.settings['max_depth'],
            allowed_domains=self.settings['allowed_domains'] if self.settings['allowed_domains'] else None,
            link_filters=self.settings['link_filters'] if self.settings['link_filters'] else None,
            max_retries=self.settings['max_retries'],
            retry_backoff=self.settings['retry_backoff'],
            # Phase 3 settings
            proxies=self.settings['proxies'] if self.settings['proxies'] else None,
            randomize_delay=self.settings['randomize_delay'],
            delay_min=self.settings['delay_min'],
            delay_max=self.settings['delay_max'],
            include_keywords=self.settings['include_keywords'] if self.settings['include_keywords'] else None,
            exclude_keywords=self.settings['exclude_keywords'] if self.settings['exclude_keywords'] else None
        )
        
        self.var_status.set("Crawling...")
        log_safe(self.log, f"Starting crawl from {root}")
        if self.settings['respect_robots']:
            log_safe(self.log, "Robots.txt compliance: ENABLED")
        if self.settings['max_depth'] >= 0:
            log_safe(self.log, f"Max crawl depth: {self.settings['max_depth']}")

        def progress_cb(title: str, html: str):
            # push to UI queue; heavy parsing for text/xml happens on UI thread
            self.queue_preview.put((title, html))
            
            # Phase 2: Screenshot per page (save HTML file)
            if self.screenshot_per_page and self.screenshot_dir:
                try:
                    # Get URL from spider
                    if self.spider and self.spider.rows:
                        url = self.spider.rows[-1].url
                        # Sanitize filename
                        safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_')).rstrip()
                        safe_title = safe_title[:50]  # Limit length
                        safe_url = urlparse.urlparse(url).path.replace('/', '_').replace('\\', '_')
                        if not safe_url or safe_url == '_':
                            safe_url = 'index'
                        
                        filename = f"{safe_title}_{safe_url}.html"
                        filename = "".join(c for c in filename if c.isalnum() or c in (' ', '-', '_', '.'))
                        
                        filepath = os.path.join(self.screenshot_dir, filename)
                        # Handle duplicates
                        counter = 1
                        base_name, ext = os.path.splitext(filename)
                        while os.path.exists(filepath):
                            filepath = os.path.join(self.screenshot_dir, f"{base_name}_{counter}{ext}")
                            counter += 1
                        
                        with open(filepath, 'w', encoding='utf-8') as f:
                            f.write(html)
                        self.after(0, lambda: log_safe(self.log, f"Saved page HTML: {os.path.basename(filepath)}"))
                except Exception as e:
                    self.after(0, lambda: log_safe(self.log, f"Failed to save page HTML: {e}"))

        def log_cb(msg: str):
            self.after(0, lambda: log_safe(self.log, msg))

        def run():
            try:
                assert self.spider is not None
                self.spider.crawl(progress_cb=progress_cb, log_cb=log_cb)
            finally:
                self.after(0, lambda: self.var_status.set("Done" if not (self.spider and self.spider.cancelled) else "Stopped"))

        self.spider_thread = threading.Thread(target=run, daemon=True)
        self.spider_thread.start()

    def _drain_preview_queue(self):
        try:
            while True:
                title, html = self.queue_preview.get_nowait()
                # Store row (URL will be available in spider.state)
                # Find the row just added by Spider
                if self.spider:
                    row = self.spider.rows[-1]
                    self.rows.append(row)
                    row_idx = len(self.rows) - 1
                    
                    # Check if row passes filter
                    filter_text = self.var_filter.get().lower().strip()
                    if not filter_text or (filter_text in row.title.lower() or 
                                          filter_text in row.category.lower() or 
                                          filter_text in row.url.lower()):
                        item_id = self._insert_tree_row(row, row_idx, highlight=True)
                        self._highlight_row(item_id)
                    
                    # Update progress bar
                    pages_count = len(self.rows)
                    self.var_pages_scraped.set(f"Pages scraped: {pages_count}")
                    self.progress_bar['value'] = pages_count
                    
                    if len(self.rows) == 1:
                        self._show_document(title, html)
        except queue.Empty:
            pass
        self.after(75, self._drain_preview_queue)

    def _show_document(self, title: str, html: str):
        self.var_page_title.set(title[:120])
        self.current_html = html
        
        # Clear all views
        self.txt_html.delete("1.0", "end")
        self.txt_text.delete("1.0", "end")
        self.txt_xml.delete("1.0", "end")
        self.txt_css.delete("1.0", "end")
        self.txt_js.delete("1.0", "end")
        self.txt_metadata.delete("1.0", "end")
        self.txt_tables.delete("1.0", "end")
        self.txt_json.configure(state="normal")
        self.txt_json.delete("1.0", "end")
        self.txt_json.configure(state="disabled")
        self.txt_regex.delete("1.0", "end")
        
        # Clear scraped images when page changes
        self.scraped_images.clear()
        self.image_checkboxes.clear()
        self._clear_images_display()
        self.var_images_count.set("No images scraped")

        soup = BeautifulSoup(html, "lxml")
        
        # Text view
        text = soup.get_text(separator="\n", strip=True)
        self.txt_text.insert("1.0", text)

        # simple XML-ish representation (not a full DOM)
        xml_out = ["<page>"]
        xml_out.append(f"  <title>{(soup.title.string.strip() if soup.title and soup.title.string else title)}</title>")
        xml_out.append("  <links>")
        for a in soup.select("a[href]")[:300]:
            href = a.get("href", "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            txt = (a.get_text(strip=True) or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            xml_out.append(f'    <a href="{href}">{txt}</a>')
        xml_out.append("  </links>")
        xml_out.append("</page>")
        self.txt_xml.insert("1.0", "\n".join(xml_out))

        # HTML Preview
        self.txt_html.insert("1.0", html)
        
        # Extract and display CSS
        css_content = self._extract_css(soup, html)
        self.txt_css.insert("1.0", css_content)
        
        # Extract and display JavaScript
        js_content = self._extract_javascript(soup, html)
        self.txt_js.insert("1.0", js_content)
        
        # Phase 2: Extract metadata
        metadata_content = self._extract_metadata(soup, html)
        self.txt_metadata.insert("1.0", metadata_content)
        
        # Phase 2: Extract tables
        tables_content = self._extract_tables(soup, html)
        self.txt_tables.insert("1.0", tables_content)
        
        # Phase 2: Extract JSON
        json_content = self._extract_json(soup, html)
        self.txt_json.configure(state="normal")
        self.txt_json.delete("1.0", "end")
        self.txt_json.insert("1.0", json_content)
        self.txt_json.configure(state="disabled")
    
    def _extract_css(self, soup: BeautifulSoup, html: str) -> str:
        """Extract all CSS from the HTML"""
        css_parts = []
        
        # Extract inline styles from style attribute
        inline_styles = []
        for element in soup.find_all(style=True):
            style_attr = element.get('style', '')
            if style_attr:
                inline_styles.append(f"/* Inline style from <{element.name}> */\n{style_attr}\n")
        
        if inline_styles:
            css_parts.append("/* === INLINE STYLES === */\n")
            css_parts.extend(inline_styles)
            css_parts.append("\n")
        
        # Extract <style> tags
        style_tags = soup.find_all('style')
        if style_tags:
            css_parts.append("/* === STYLE TAGS === */\n")
            for idx, style_tag in enumerate(style_tags, 1):
                css_content = style_tag.string or ""
                if css_content.strip():
                    css_parts.append(f"/* Style tag #{idx} */\n{css_content}\n\n")
        
        # Extract external CSS links
        css_links = soup.find_all('link', rel='stylesheet')
        if css_links:
            css_parts.append("/* === EXTERNAL CSS LINKS === */\n")
            for link in css_links:
                href = link.get('href', '')
                if href:
                    # Try to resolve relative URLs
                    if self.current_url:
                        full_url = urlparse.urljoin(self.current_url, href)
                    else:
                        full_url = href
                    css_parts.append(f"/* External CSS: {full_url} */\n@import url('{full_url}');\n\n")
        
        if not css_parts:
            return "/* No CSS found in this page */"
        
        return "".join(css_parts)
    
    def _extract_javascript(self, soup: BeautifulSoup, html: str) -> str:
        """Extract all JavaScript from the HTML"""
        js_parts = []
        
        # Extract inline JavaScript from <script> tags
        script_tags = soup.find_all('script')
        if script_tags:
            for idx, script_tag in enumerate(script_tags, 1):
                script_type = script_tag.get('type', 'text/javascript')
                src = script_tag.get('src', '')
                
                if src:
                    # External JavaScript
                    if self.current_url:
                        full_url = urlparse.urljoin(self.current_url, src)
                    else:
                        full_url = src
                    js_parts.append(f"/* External JavaScript #{idx}: {full_url} */\n")
                    js_parts.append(f"// <script src=\"{src}\" type=\"{script_type}\"></script>\n\n")
                else:
                    # Inline JavaScript
                    script_content = script_tag.string or ""
                    if script_content.strip():
                        js_parts.append(f"/* Inline JavaScript #{idx} (type: {script_type}) */\n")
                        js_parts.append(f"{script_content}\n\n")
        
        # Extract JavaScript from event handlers (onclick, onload, etc.)
        event_handlers = []
        for element in soup.find_all(True):  # Find all elements
            for attr in element.attrs:
                if attr.startswith('on') and attr[2:].islower():  # onclick, onload, etc.
                    handler_code = element.get(attr, '')
                    if handler_code:
                        event_handlers.append(f"/* Event handler: {attr} on <{element.name}> */\n{handler_code}\n")
        
        if event_handlers:
            js_parts.append("/* === EVENT HANDLERS === */\n")
            js_parts.extend(event_handlers)
            js_parts.append("\n")
        
        if not js_parts:
            return "// No JavaScript found in this page"
        
        return "".join(js_parts)
    
    def _extract_metadata(self, soup: BeautifulSoup, html: str) -> str:
        """Extract metadata tags (meta, OG tags, etc.)"""
        metadata_parts = []
        
        # Standard meta tags
        meta_tags = soup.find_all('meta')
        if meta_tags:
            metadata_parts.append("=== STANDARD META TAGS ===\n")
            for meta in meta_tags:
                name = meta.get('name') or meta.get('property') or meta.get('http-equiv', '')
                content = meta.get('content', '')
                charset = meta.get('charset', '')
                
                if charset:
                    metadata_parts.append(f"charset: {charset}\n")
                elif name and content:
                    metadata_parts.append(f"{name}: {content}\n")
                elif content:
                    metadata_parts.append(f"meta: {content}\n")
            metadata_parts.append("\n")
        
        # Open Graph tags
        og_tags = soup.find_all('meta', property=lambda x: x and x.startswith('og:'))
        if og_tags:
            metadata_parts.append("=== OPEN GRAPH TAGS ===\n")
            for og in og_tags:
                property_name = og.get('property', '')
                content = og.get('content', '')
                if property_name and content:
                    metadata_parts.append(f"{property_name}: {content}\n")
            metadata_parts.append("\n")
        
        # Twitter Card tags
        twitter_tags = soup.find_all('meta', attrs={'name': lambda x: x and x.startswith('twitter:')})
        if twitter_tags:
            metadata_parts.append("=== TWITTER CARD TAGS ===\n")
            for twitter in twitter_tags:
                name = twitter.get('name', '')
                content = twitter.get('content', '')
                if name and content:
                    metadata_parts.append(f"{name}: {content}\n")
            metadata_parts.append("\n")
        
        # Schema.org JSON-LD
        json_ld = soup.find_all('script', type='application/ld+json')
        if json_ld:
            metadata_parts.append("=== SCHEMA.ORG JSON-LD ===\n")
            for idx, script in enumerate(json_ld, 1):
                content = script.string or ""
                if content.strip():
                    metadata_parts.append(f"JSON-LD #{idx}:\n{content}\n\n")
        
        # Canonical URL
        canonical = soup.find('link', rel='canonical')
        if canonical:
            metadata_parts.append("=== CANONICAL URL ===\n")
            metadata_parts.append(f"canonical: {canonical.get('href', '')}\n\n")
        
        if not metadata_parts:
            return "No metadata found in this page"
        
        return "".join(metadata_parts)
    
    def _extract_tables(self, soup: BeautifulSoup, html: str) -> str:
        """Extract and parse HTML tables"""
        tables = soup.find_all('table')
        if not tables:
            return "No tables found in this page"
        
        table_parts = []
        for idx, table in enumerate(tables, 1):
            table_parts.append(f"=== TABLE #{idx} ===\n")
            
            # Extract headers
            headers = []
            thead = table.find('thead')
            if thead:
                header_rows = thead.find_all('tr')
                for row in header_rows:
                    cells = row.find_all(['th', 'td'])
                    row_headers = [cell.get_text(strip=True) for cell in cells]
                    if row_headers:
                        headers.append(row_headers)
            
            # If no thead, check first row for headers
            if not headers:
                first_row = table.find('tr')
                if first_row:
                    cells = first_row.find_all(['th', 'td'])
                    row_headers = [cell.get_text(strip=True) for cell in cells]
                    if row_headers:
                        headers.append(row_headers)
            
            if headers:
                table_parts.append("Headers:\n")
                for header_row in headers:
                    table_parts.append(f"  {' | '.join(header_row)}\n")
                table_parts.append("\n")
            
            # Extract rows
            tbody = table.find('tbody') or table
            rows = tbody.find_all('tr')
            
            if rows:
                table_parts.append("Rows:\n")
                for row in rows[:100]:  # Limit to 100 rows
                    cells = row.find_all(['td', 'th'])
                    cell_texts = [cell.get_text(strip=True) for cell in cells]
                    if cell_texts:
                        table_parts.append(f"  {' | '.join(cell_texts)}\n")
                
                if len(rows) > 100:
                    table_parts.append(f"\n... ({len(rows) - 100} more rows)\n")
            
            table_parts.append("\n")
        
        return "".join(table_parts)
    
    def _extract_json(self, soup: BeautifulSoup, html: str) -> str:
        """Extract JSON and JSON-LD from HTML"""
        json_parts = []
        
        # JSON-LD (already handled in metadata, but show here too)
        json_ld = soup.find_all('script', type='application/ld+json')
        if json_ld:
            json_parts.append("=== JSON-LD (Schema.org) ===\n")
            for idx, script in enumerate(json_ld, 1):
                content = script.string or ""
                if content.strip():
                    try:
                        # Try to pretty-print JSON
                        parsed = json.loads(content)
                        pretty = json.dumps(parsed, indent=2, ensure_ascii=False)
                        json_parts.append(f"JSON-LD #{idx}:\n{pretty}\n\n")
                    except:
                        json_parts.append(f"JSON-LD #{idx} (raw):\n{content}\n\n")
        
        # Find JSON in script tags
        script_tags = soup.find_all('script')
        json_scripts = []
        for script in script_tags:
            content = script.string or ""
            if content.strip():
                # Try to detect JSON-like content
                content_stripped = content.strip()
                if (content_stripped.startswith('{') and content_stripped.endswith('}')) or \
                   (content_stripped.startswith('[') and content_stripped.endswith(']')):
                    try:
                        parsed = json.loads(content)
                        json_scripts.append((script, content, parsed))
                    except:
                        pass
        
        if json_scripts:
            json_parts.append("=== JSON IN SCRIPT TAGS ===\n")
            for idx, (script, raw, parsed) in enumerate(json_scripts, 1):
                script_id = script.get('id', '')
                script_type = script.get('type', '')
                json_parts.append(f"JSON #{idx}")
                if script_id:
                    json_parts.append(f" (id: {script_id})")
                if script_type:
                    json_parts.append(f" (type: {script_type})")
                json_parts.append(":\n")
                try:
                    pretty = json.dumps(parsed, indent=2, ensure_ascii=False)
                    json_parts.append(f"{pretty}\n\n")
                except:
                    json_parts.append(f"{raw}\n\n")
        
        # Find JSON in data attributes
        json_data_attrs = []
        for element in soup.find_all(True):
            for attr, value in element.attrs.items():
                if isinstance(value, str) and value.strip():
                    value_stripped = value.strip()
                    if (value_stripped.startswith('{') and value_stripped.endswith('}')) or \
                       (value_stripped.startswith('[') and value_stripped.endswith(']')):
                        try:
                            parsed = json.loads(value)
                            json_data_attrs.append((element.name, attr, value, parsed))
                        except:
                            pass
        
        if json_data_attrs:
            json_parts.append("=== JSON IN DATA ATTRIBUTES ===\n")
            for tag, attr, raw, parsed in json_data_attrs:
                json_parts.append(f"<{tag}> {attr}:\n")
                try:
                    pretty = json.dumps(parsed, indent=2, ensure_ascii=False)
                    json_parts.append(f"{pretty}\n\n")
                except:
                    json_parts.append(f"{raw}\n\n")
        
        if not json_parts:
            return "No JSON found in this page"
        
        return "".join(json_parts)
    
    def on_regex_search(self):
        """Search HTML/text with regex pattern"""
        pattern = self.var_regex_pattern.get().strip()
        if not pattern:
            messagebox.showwarning(APP_TITLE, "Enter a regex pattern to search.")
            return
        
        if not self.current_html:
            messagebox.showinfo(APP_TITLE, "No page loaded. Please select a page first.")
            return
        
        try:
            # Search in HTML
            html_matches = re.finditer(pattern, self.current_html, re.IGNORECASE | re.MULTILINE)
            html_results = []
            for match in html_matches:
                html_results.append(f"HTML Match at position {match.start()}: {match.group()[:200]}")
            
            # Search in text
            soup = BeautifulSoup(self.current_html, "lxml")
            text = soup.get_text()
            text_matches = re.finditer(pattern, text, re.IGNORECASE | re.MULTILINE)
            text_results = []
            for match in text_matches:
                text_results.append(f"Text Match at position {match.start()}: {match.group()[:200]}")
            
            # Display results
            self.txt_regex.delete("1.0", "end")
            results = []
            results.append(f"Regex Pattern: {pattern}\n")
            results.append("=" * 60 + "\n\n")
            
            if html_results:
                results.append(f"=== HTML MATCHES ({len(html_results)}) ===\n")
                for result in html_results[:100]:  # Limit to 100 matches
                    results.append(f"{result}\n")
                if len(html_results) > 100:
                    results.append(f"\n... ({len(html_results) - 100} more matches)\n")
                results.append("\n")
            
            if text_results:
                results.append(f"=== TEXT MATCHES ({len(text_results)}) ===\n")
                for result in text_results[:100]:  # Limit to 100 matches
                    results.append(f"{result}\n")
                if len(text_results) > 100:
                    results.append(f"\n... ({len(text_results) - 100} more matches)\n")
            
            if not html_results and not text_results:
                results.append("No matches found.\n")
            
            self.txt_regex.insert("1.0", "".join(results))
            
        except re.error as e:
            messagebox.showerror(APP_TITLE, f"Invalid regex pattern: {e}")
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"Error searching: {e}")
    
    def on_regex_clear(self):
        """Clear regex search results"""
        self.txt_regex.delete("1.0", "end")
        self.var_regex_pattern.set("")

    def _insert_tree_row(self, row: Row, row_idx: int, highlight: bool = False) -> str:
        """Insert a row into the tree with alternating colors"""
        tag = "evenrow" if row_idx % 2 == 0 else "oddrow"
        tags_list = [str(row_idx), tag]
        
        if highlight:
            tags_list.append("highlight")
        
        item_id = self.tree.insert("", "end", values=(row.title, row.category, row.url), tags=tuple(tags_list))
        self.tree_item_ids[str(row_idx)] = item_id
        return item_id
    
    def _refresh_tree(self):
        """Refresh the tree view with current filter"""
        # Clear tree
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        
        # Apply filter
        filter_text = self.var_filter.get().lower().strip()
        if filter_text:
            filtered_rows = [
                (idx, row) for idx, row in enumerate(self.rows)
                if filter_text in row.title.lower() or 
                   filter_text in row.category.lower() or 
                   filter_text in row.url.lower()
            ]
        else:
            filtered_rows = [(idx, row) for idx, row in enumerate(self.rows)]
        
        # Insert filtered rows
        for display_idx, (original_idx, row) in enumerate(filtered_rows):
            self._insert_tree_row(row, original_idx, highlight=False)
    
    def on_filter_changed(self):
        """Handle filter text change"""
        self._refresh_tree()
    
    def on_tree_double_click(self, event):
        """Handle double-click on tree item to open URL in browser"""
        sel = self.tree.selection()
        if not sel:
            return
        
        item_id = sel[0]
        tags = self.tree.item(item_id, "tags")
        if tags and tags[0].isdigit():
            idx = int(tags[0])
            if 0 <= idx < len(self.rows):
                webbrowser.open(self.rows[idx].url)
    
    def _highlight_row(self, item_id: str):
        """Flash highlight a row with yellow"""
        # Add highlight tag temporarily
        try:
            current_tags = list(self.tree.item(item_id, "tags"))
            if "highlight" not in current_tags:
                current_tags.append("highlight")
                self.tree.item(item_id, tags=tuple(current_tags))
                # Remove highlight after 2 seconds
                self.after(2000, lambda: self._remove_highlight(item_id, current_tags))
        except Exception:
            # Item might not exist anymore, ignore
            pass
    
    def _remove_highlight(self, item_id: str, original_tags: list):
        """Remove highlight from a row"""
        try:
            if "highlight" in original_tags:
                original_tags.remove("highlight")
            self.tree.item(item_id, tags=tuple(original_tags))
        except:
            pass
    
    def on_row_selected(self, _evt=None):
        sel = self.tree.selection()
        if not sel:
            return
        # Get row index from tags
        item_id = sel[0]
        tags = self.tree.item(item_id, "tags")
        if tags and tags[0].isdigit():
            idx = int(tags[0])
            if 0 <= idx < len(self.rows):
                row = self.rows[idx]
                self.current_url = row.url
                self.var_page_title.set(row.title[:120])
                # fetch fresh for preview (lightweight)
                try:
                    r = requests.get(row.url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
                    r.raise_for_status()
                    self._show_document(row.title, r.text)
                except Exception as e:
                    messagebox.showerror(APP_TITLE, f"Failed to load page:\n{e}")
    
    def on_column_click(self, column: str):
        """Handle column header click for sorting"""
        if self.sort_column == column:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_column = column
            self.sort_reverse = False
        
        # Sort rows
        reverse = self.sort_reverse
        if column == "title":
            self.rows.sort(key=lambda r: r.title.lower(), reverse=reverse)
        elif column == "category":
            self.rows.sort(key=lambda r: r.category.lower(), reverse=reverse)
        elif column == "url":
            self.rows.sort(key=lambda r: r.url.lower(), reverse=reverse)
        
        # Update treeview with filter
        self._refresh_tree()
        
        # Update heading to show sort direction
        arrow = " ↓" if reverse else " ↑"
        for col in ["title", "category", "url"]:
            text = col.capitalize()
            if col == column:
                text += arrow
            self.tree.heading(col, text=text)
    
    def on_right_click(self, event):
        """Handle right-click context menu"""
        sel = self.tree.selection()
        if not sel:
            return
        
        # Create context menu
        menu = tk.Menu(self, tearoff=0)
        
        if len(sel) == 1:
            menu.add_command(label="Change Category", command=self.on_change_category)
            menu.add_separator()
            menu.add_command(label="Visit in Browser", command=self.on_visit_selected)
            menu.add_command(label="Scrape Images", command=self.on_scrape_images_from_selected)
        else:
            menu.add_command(label=f"Change Category ({len(sel)} items)", command=self.on_change_category)
            menu.add_separator()
            menu.add_command(label=f"Visit {len(sel)} Pages in Browser", command=self.on_visit_selected)
        
        menu.add_separator()
        
        if len(sel) == 1:
            menu.add_command(label="Export as XML", command=lambda: self.on_export_selected("xml"))
            menu.add_command(label="Export as HTML", command=lambda: self.on_export_selected("html"))
        else:
            menu.add_command(label=f"Export {len(sel)} as XML", command=lambda: self.on_export_selected("xml"))
            menu.add_command(label=f"Export {len(sel)} as HTML", command=lambda: self.on_export_selected("html"))
        
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
    
    def on_visit_selected(self):
        """Open selected items in browser"""
        sel = self.tree.selection()
        for item_id in sel:
            tags = self.tree.item(item_id, "tags")
            if tags and tags[0].isdigit():
                idx = int(tags[0])
                if 0 <= idx < len(self.rows):
                    webbrowser.open(self.rows[idx].url)
    
    def on_scrape_images_from_selected(self):
        """Scrape images from the selected item's page"""
        sel = self.tree.selection()
        if not sel:
            return
        
        # Get the first selected item (only scrape from one page at a time)
        item_id = sel[0]
        tags = self.tree.item(item_id, "tags")
        if not tags or not tags[0].isdigit():
            return
        
        idx = int(tags[0])
        if idx < 0 or idx >= len(self.rows):
            return
        
        row = self.rows[idx]
        url = row.url
        
        log_safe(self.log, f"Scraping images from {url}...")
        self.var_status.set(f"Scraping images from {row.title[:50]}...")
        
        # Load the page and scrape images in a thread
        def scrape_thread():
            try:
                # Fetch the page HTML
                r = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
                r.raise_for_status()
                html = r.text
                title = row.title
                
                # Extract images
                images = self._extract_images(html, url)
                
                # Update UI on main thread
                self.after(0, lambda: self._display_images(images))
                self.after(0, lambda: log_safe(self.log, f"Found {len(images)} image(s) from {row.title[:50]}..."))
                self.after(0, lambda: self.var_status.set("Ready"))
                
                # Also switch to images tab
                self.after(0, lambda: self._switch_to_images_tab())
                
            except Exception as e:
                self.after(0, lambda: messagebox.showerror(APP_TITLE, f"Error scraping images: {e}"))
                self.after(0, lambda: self.var_status.set("Ready"))
        
        threading.Thread(target=scrape_thread, daemon=True).start()
    
    def _switch_to_images_tab(self):
        """Switch to the Scraped Images tab"""
        try:
            # Find the index of the images tab
            for i in range(self.nb.index("end")):
                if self.nb.tab(i, "text") == "Scraped Images":
                    self.nb.select(i)
                    break
        except:
            pass
    
    def on_export_selected(self, format_type: str):
        """Export selected items as XML or HTML"""
        sel = self.tree.selection()
        if not sel:
            return
        
        selected_rows = []
        for item_id in sel:
            tags = self.tree.item(item_id, "tags")
            if tags and tags[0].isdigit():
                idx = int(tags[0])
                if 0 <= idx < len(self.rows):
                    selected_rows.append(self.rows[idx])
        
        if not selected_rows:
            return
        
        if format_type == "xml":
            path = filedialog.asksaveasfilename(defaultextension=".xml", filetypes=[("XML", "*.xml")], title="Save selected as XML")
            if not path:
                return
            lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
            for r in selected_rows:
                lines.append("  <url>")
                lines.append(f"    <loc>{r.url}</loc>")
                lines.append("  </url>")
            lines.append("</urlset>")
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            log_safe(self.log, f"Exported {len(selected_rows)} items as XML: {path}")
        
        elif format_type == "html":
            path = filedialog.asksaveasfilename(defaultextension=".html", filetypes=[("HTML", "*.html")], title="Save selected as HTML")
            if not path:
                return
            html = [
                "<!doctype html><meta charset='utf-8'><title>Selected Sitemap</title>",
                "<style>body{font-family:system-ui,Segoe UI,Arial} table{border-collapse:collapse} td,th{border:1px solid #ccc;padding:6px 10px}</style>",
                "<h1>Selected Sitemap</h1>",
                "<table><thead><tr><th>Title</th><th>Category</th><th>URL</th></tr></thead><tbody>",
            ]
            for r in selected_rows:
                html.append(f"<tr><td>{r.title}</td><td>{r.category}</td><td><a href='{r.url}'>{r.url}</a></td></tr>")
            html.append("</tbody></table>")
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(html))
            log_safe(self.log, f"Exported {len(selected_rows)} items as HTML: {path}")
    
    def on_change_category(self):
        """Change category for selected items"""
        sel = self.tree.selection()
        if not sel:
            return
        
        # Get selected rows
        selected_rows = []
        selected_indices = []
        for item_id in sel:
            tags = self.tree.item(item_id, "tags")
            if tags and tags[0].isdigit():
                idx = int(tags[0])
                if 0 <= idx < len(self.rows):
                    selected_rows.append(self.rows[idx])
                    selected_indices.append(idx)
        
        if not selected_rows:
            return
        
        # Get current category (for single selection) or show prompt
        if len(selected_rows) == 1:
            current_cat = selected_rows[0].category
            prompt = f"Enter new category for:\n{selected_rows[0].title[:50]}..."
        else:
            current_cat = ""
            prompt = f"Enter new category for {len(selected_rows)} items:"
        
        # Create dialog
        dialog = tk.Toplevel(self)
        dialog.title("Change Category")
        dialog.geometry("400x150")
        dialog.transient(self)
        dialog.grab_set()
        
        ttk.Label(dialog, text=prompt, wraplength=350).pack(pady=10)
        
        var_new_category = tk.StringVar(value=current_cat)
        ent_category = ttk.Entry(dialog, textvariable=var_new_category, width=40)
        ent_category.pack(pady=10)
        ent_category.focus()
        ent_category.select_range(0, tk.END)
        
        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(pady=10)
        
        def save_category():
            new_cat = var_new_category.get().strip()
            if not new_cat:
                messagebox.showwarning(APP_TITLE, "Category cannot be empty.")
                return
            
            # Update rows
            for idx in selected_indices:
                self.rows[idx].category = new_cat
            
            # Update treeview
            for item_id in sel:
                tags = self.tree.item(item_id, "tags")
                if tags and tags[0].isdigit():
                    idx = int(tags[0])
                    if 0 <= idx < len(self.rows):
                        row = self.rows[idx]
                        self.tree.item(item_id, values=(row.title, row.category, row.url))
            
            log_safe(self.log, f"Changed category to '{new_cat}' for {len(selected_rows)} item(s)")
            dialog.destroy()
        
        ttk.Button(btn_frame, text="Save", command=save_category).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Cancel", command=dialog.destroy).pack(side="left", padx=5)
        
        # Bind Enter key
        ent_category.bind("<Return>", lambda e: save_category())
    
    def on_regex_entry_right_click(self, event):
        """Right-click context menu for regex entry"""
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="Copy", command=lambda: self._copy_selection(self.ent_regex))
        menu.add_command(label="Paste", command=lambda: self._paste_to_entry(self.ent_regex))
        menu.add_command(label="Cut", command=lambda: self._cut_selection(self.ent_regex))
        
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
    
    def on_json_right_click(self, event):
        """Right-click context menu for JSON preview (read-only, copy only)"""
        menu = tk.Menu(self, tearoff=0)
        
        # Check if there's a selection
        try:
            if self.txt_json.tag_ranges(tk.SEL):
                menu.add_command(label="Copy", command=lambda: self._copy_from_text(self.txt_json))
            else:
                # No selection, copy all
                menu.add_command(label="Copy All", command=lambda: self._copy_all_from_text(self.txt_json))
        except:
            menu.add_command(label="Copy All", command=lambda: self._copy_all_from_text(self.txt_json))
        
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
    
    def on_text_preview_right_click(self, event):
        """Right-click context menu for text preview areas (with screenshot option)"""
        widget = event.widget
        menu = tk.Menu(self, tearoff=0)
        
        # Standard text operations
        try:
            if widget.tag_ranges(tk.SEL):
                menu.add_command(label="Copy", command=lambda: self._copy_from_text(widget))
                menu.add_command(label="Cut", command=lambda: self._cut_from_text(widget))
            else:
                menu.add_command(label="Copy All", command=lambda: self._copy_all_from_text(widget))
        except:
            menu.add_command(label="Copy All", command=lambda: self._copy_all_from_text(widget))
        
        menu.add_separator()
        
        # Screenshot options
        menu.add_command(label="Screenshot Selected Lines", command=lambda: self._screenshot_selected_lines(widget))
        menu.add_command(label="Screenshot Entire Page", command=lambda: self._screenshot_page())
        
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
    
    def _copy_selection(self, widget):
        """Copy selected text from entry widget"""
        try:
            if widget.selection_present():
                self.clipboard_clear()
                self.clipboard_append(widget.selection_get())
        except:
            pass
    
    def _cut_selection(self, widget):
        """Cut selected text from entry widget"""
        try:
            if widget.selection_present():
                self.clipboard_clear()
                self.clipboard_append(widget.selection_get())
                widget.delete(tk.SEL_FIRST, tk.SEL_LAST)
        except:
            pass
    
    def _paste_to_entry(self, widget):
        """Paste clipboard content to entry widget"""
        try:
            widget.delete(tk.SEL_FIRST, tk.SEL_LAST)
        except:
            pass
        try:
            widget.insert(tk.INSERT, self.clipboard_get())
        except:
            pass
    
    def _copy_from_text(self, widget):
        """Copy selected text from text widget"""
        try:
            if widget.tag_ranges(tk.SEL):
                text = widget.get(tk.SEL_FIRST, tk.SEL_LAST)
                self.clipboard_clear()
                self.clipboard_append(text)
        except:
            pass
    
    def _copy_all_from_text(self, widget):
        """Copy all text from text widget"""
        try:
            text = widget.get("1.0", "end-1c")
            self.clipboard_clear()
            self.clipboard_append(text)
        except:
            pass
    
    def _cut_from_text(self, widget):
        """Cut selected text from text widget"""
        try:
            if widget.tag_ranges(tk.SEL):
                text = widget.get(tk.SEL_FIRST, tk.SEL_LAST)
                self.clipboard_clear()
                self.clipboard_append(text)
                widget.delete(tk.SEL_FIRST, tk.SEL_LAST)
        except:
            pass
    
    def _screenshot_selected_lines(self, widget):
        """Screenshot the website page for selected lines"""
        if not self.current_url:
            messagebox.showinfo(APP_TITLE, "No page loaded. Please select a page first.")
            return
        
        if ImageGrab is None:
            messagebox.showinfo(APP_TITLE, "Install Pillow for screenshots: pip install pillow")
            return
        
        try:
            # Get selected text lines
            if widget.tag_ranges(tk.SEL):
                start = widget.index(tk.SEL_FIRST)
                end = widget.index(tk.SEL_LAST)
                start_line = int(start.split('.')[0])
                end_line = int(end.split('.')[0])
            else:
                messagebox.showinfo(APP_TITLE, "Please select text lines to screenshot.")
                return
            
            # Open browser and take screenshot
            webbrowser.open(self.current_url)
            time.sleep(2)  # Wait for page to load
            
            # Take screenshot of entire screen (user can crop manually)
            img = ImageGrab.grab()
            
            # Save screenshot
            safe_title = "".join(c for c in self.var_page_title.get()[:30] if c.isalnum() or c in (' ', '-', '_')).strip()
            if not safe_title:
                safe_title = "screenshot"
            
            filename = f"{safe_title}_lines_{start_line}_{end_line}.png"
            filename = "".join(c for c in filename if c.isalnum() or c in (' ', '-', '_', '.'))
            
            path = filedialog.asksaveasfilename(
                defaultextension=".png",
                filetypes=[("PNG", "*.png")],
                title="Save Screenshot",
                initialfile=filename
            )
            
            if path:
                img.save(path)
                log_safe(self.log, f"Saved screenshot: {path}")
                messagebox.showinfo(APP_TITLE, f"Screenshot saved to:\n{path}\n\nNote: This captures the entire screen. Crop as needed.")
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"Error taking screenshot: {e}")
    
    def _screenshot_page(self):
        """Screenshot the entire website page"""
        if not self.current_url:
            messagebox.showinfo(APP_TITLE, "No page loaded. Please select a page first.")
            return
        
        if ImageGrab is None:
            messagebox.showinfo(APP_TITLE, "Install Pillow for screenshots: pip install pillow")
            return
        
        try:
            # Open browser and take screenshot
            webbrowser.open(self.current_url)
            time.sleep(2)  # Wait for page to load
            
            # Take screenshot of entire screen
            img = ImageGrab.grab()
            
            # Save screenshot
            safe_title = "".join(c for c in self.var_page_title.get()[:30] if c.isalnum() or c in (' ', '-', '_')).strip()
            if not safe_title:
                safe_title = "screenshot"
            
            filename = f"{safe_title}_page.png"
            filename = "".join(c for c in filename if c.isalnum() or c in (' ', '-', '_', '.'))
            
            path = filedialog.asksaveasfilename(
                defaultextension=".png",
                filetypes=[("PNG", "*.png")],
                title="Save Screenshot",
                initialfile=filename
            )
            
            if path:
                img.save(path)
                log_safe(self.log, f"Saved screenshot: {path}")
                messagebox.showinfo(APP_TITLE, f"Screenshot saved to:\n{path}\n\nNote: This captures the entire screen. Crop as needed.")
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"Error taking screenshot: {e}")
    
    def on_settings(self):
        """Open Settings dialog for Phase 1 features"""
        dialog = tk.Toplevel(self)
        dialog.title("Advanced Settings")
        dialog.geometry("700x650")
        dialog.transient(self)
        dialog.grab_set()
        
        # Create notebook for tabs
        nb = ttk.Notebook(dialog)
        nb.pack(fill="both", expand=True, padx=10, pady=10)
        
        # === General Tab ===
        general_frame = ttk.Frame(nb, padding=10)
        nb.add(general_frame, text="General")
        
        row = 0
        
        # User-Agent
        ttk.Label(general_frame, text="User-Agent:").grid(row=row, column=0, sticky="w", pady=5)
        var_ua = tk.StringVar(value=self.settings['user_agent'])
        ent_ua = ttk.Entry(general_frame, textvariable=var_ua, width=60)
        ent_ua.grid(row=row, column=1, sticky="ew", pady=5, padx=5)
        general_frame.columnconfigure(1, weight=1)
        row += 1
        
        var_randomize_ua = tk.BooleanVar(value=self.settings['randomize_ua'])
        ttk.Checkbutton(general_frame, text="Randomize User-Agent", variable=var_randomize_ua).grid(row=row, column=0, columnspan=2, sticky="w", pady=5)
        row += 1
        
        # Robots.txt
        var_respect_robots = tk.BooleanVar(value=self.settings['respect_robots'])
        ttk.Checkbutton(general_frame, text="Respect robots.txt", variable=var_respect_robots).grid(row=row, column=0, columnspan=2, sticky="w", pady=5)
        row += 1
        
        # Max Depth
        ttk.Label(general_frame, text="Max Crawl Depth (-1 = unlimited):").grid(row=row, column=0, sticky="w", pady=5)
        var_max_depth = tk.StringVar(value=str(self.settings['max_depth']))
        ent_depth = ttk.Entry(general_frame, textvariable=var_max_depth, width=10)
        ent_depth.grid(row=row, column=1, sticky="w", pady=5, padx=5)
        row += 1
        
        # Retry Settings
        ttk.Label(general_frame, text="Max Retries:").grid(row=row, column=0, sticky="w", pady=5)
        var_max_retries = tk.StringVar(value=str(self.settings['max_retries']))
        ent_retries = ttk.Entry(general_frame, textvariable=var_max_retries, width=10)
        ent_retries.grid(row=row, column=1, sticky="w", pady=5, padx=5)
        row += 1
        
        ttk.Label(general_frame, text="Retry Backoff (seconds):").grid(row=row, column=0, sticky="w", pady=5)
        var_backoff = tk.StringVar(value=str(self.settings['retry_backoff']))
        ent_backoff = ttk.Entry(general_frame, textvariable=var_backoff, width=10)
        ent_backoff.grid(row=row, column=1, sticky="w", pady=5, padx=5)
        row += 1
        
        var_randomize_delay = tk.BooleanVar(value=self.settings['randomize_delay'])
        ttk.Checkbutton(general_frame, text="Randomize delay between requests", variable=var_randomize_delay).grid(row=row, column=0, columnspan=2, sticky="w", pady=5)
        row += 1
        
        # Phase 3: Rate control with randomization
        ttk.Label(general_frame, text="Delay Min (seconds, for randomization):").grid(row=row, column=0, sticky="w", pady=5)
        var_delay_min = tk.StringVar(value=str(self.settings['delay_min']) if self.settings['delay_min'] is not None else "")
        ent_delay_min = ttk.Entry(general_frame, textvariable=var_delay_min, width=10)
        ent_delay_min.grid(row=row, column=1, sticky="w", pady=5, padx=5)
        row += 1
        
        ttk.Label(general_frame, text="Delay Max (seconds, for randomization):").grid(row=row, column=0, sticky="w", pady=5)
        var_delay_max = tk.StringVar(value=str(self.settings['delay_max']) if self.settings['delay_max'] is not None else "")
        ent_delay_max = ttk.Entry(general_frame, textvariable=var_delay_max, width=10)
        ent_delay_max.grid(row=row, column=1, sticky="w", pady=5, padx=5)
        row += 1
        
        # Phase 2: Screenshot per page
        ttk.Separator(general_frame, orient="horizontal").grid(row=row, column=0, columnspan=2, sticky="ew", pady=10)
        row += 1
        
        var_screenshot_per_page = tk.BooleanVar(value=self.screenshot_per_page)
        ttk.Checkbutton(general_frame, text="Save HTML per page (Screenshot per page)", variable=var_screenshot_per_page).grid(row=row, column=0, columnspan=2, sticky="w", pady=5)
        row += 1
        
        screenshot_dir_frame = ttk.Frame(general_frame)
        screenshot_dir_frame.grid(row=row, column=0, columnspan=2, sticky="ew", pady=5)
        screenshot_dir_frame.columnconfigure(1, weight=1)
        ttk.Label(screenshot_dir_frame, text="Save Directory:").grid(row=0, column=0, sticky="w", padx=5)
        var_screenshot_dir = tk.StringVar(value=self.screenshot_dir or "")
        ent_screenshot_dir = ttk.Entry(screenshot_dir_frame, textvariable=var_screenshot_dir, width=40)
        ent_screenshot_dir.grid(row=0, column=1, sticky="ew", padx=5)
        def browse_screenshot_dir():
            dir_path = filedialog.askdirectory(title="Select folder to save page HTML files")
            if dir_path:
                var_screenshot_dir.set(dir_path)
        ttk.Button(screenshot_dir_frame, text="Browse", command=browse_screenshot_dir).grid(row=0, column=2, padx=5)
        row += 1
        
        # === Domain & Filters Tab ===
        domain_frame = ttk.Frame(nb, padding=10)
        nb.add(domain_frame, text="Domain & Filters")
        
        row = 0
        
        # Allowed Domains
        ttk.Label(domain_frame, text="Allowed Domains (one per line, .example.com for subdomains):").grid(row=row, column=0, columnspan=2, sticky="w", pady=5)
        row += 1
        
        txt_domains = ScrolledText(domain_frame, height=6, width=50)
        txt_domains.insert("1.0", "\n".join(self.settings['allowed_domains']))
        txt_domains.grid(row=row, column=0, columnspan=2, sticky="ew", pady=5)
        domain_frame.columnconfigure(0, weight=1)
        row += 1
        
        # Link Filters
        ttk.Label(domain_frame, text="Link Filters (extensions, e.g., .html, .htm, .php):").grid(row=row, column=0, columnspan=2, sticky="w", pady=5)
        row += 1
        
        txt_filters = ScrolledText(domain_frame, height=4, width=50)
        txt_filters.insert("1.0", "\n".join(self.settings['link_filters']))
        txt_filters.grid(row=row, column=0, columnspan=2, sticky="ew", pady=5)
        row += 1
        
        # === Phase 3: Proxy & Keywords Tab ===
        proxy_frame = ttk.Frame(nb, padding=10)
        nb.add(proxy_frame, text="Proxy & Keywords")
        
        row = 0
        
        # Proxies
        ttk.Label(proxy_frame, text="Proxies (one per line, format: http://host:port or host:port):").grid(row=row, column=0, columnspan=2, sticky="w", pady=5)
        row += 1
        
        txt_proxies = ScrolledText(proxy_frame, height=6, width=50)
        txt_proxies.insert("1.0", "\n".join(self.settings['proxies']))
        txt_proxies.grid(row=row, column=0, columnspan=2, sticky="ew", pady=5)
        proxy_frame.columnconfigure(0, weight=1)
        row += 1
        
        # Keyword Filters
        ttk.Separator(proxy_frame, orient="horizontal").grid(row=row, column=0, columnspan=2, sticky="ew", pady=10)
        row += 1
        
        ttk.Label(proxy_frame, text="Include Keywords (URLs must contain at least one, one per line):").grid(row=row, column=0, columnspan=2, sticky="w", pady=5)
        row += 1
        
        txt_include_keywords = ScrolledText(proxy_frame, height=4, width=50)
        txt_include_keywords.insert("1.0", "\n".join(self.settings['include_keywords']))
        txt_include_keywords.grid(row=row, column=0, columnspan=2, sticky="ew", pady=5)
        row += 1
        
        ttk.Label(proxy_frame, text="Exclude Keywords (URLs containing these will be skipped, one per line):").grid(row=row, column=0, columnspan=2, sticky="w", pady=5)
        row += 1
        
        txt_exclude_keywords = ScrolledText(proxy_frame, height=4, width=50)
        txt_exclude_keywords.insert("1.0", "\n".join(self.settings['exclude_keywords']))
        txt_exclude_keywords.grid(row=row, column=0, columnspan=2, sticky="ew", pady=5)
        row += 1
        
        # === Headers & Cookies Tab ===
        headers_frame = ttk.Frame(nb, padding=10)
        nb.add(headers_frame, text="Headers & Cookies")
        
        row = 0
        
        # Custom Headers
        ttk.Label(headers_frame, text="Custom Headers (format: Header: Value, one per line):").grid(row=row, column=0, columnspan=2, sticky="w", pady=5)
        row += 1
        
        txt_headers = ScrolledText(headers_frame, height=6, width=50)
        headers_text = "\n".join([f"{k}: {v}" for k, v in self.settings['custom_headers'].items()])
        txt_headers.insert("1.0", headers_text)
        txt_headers.grid(row=row, column=0, columnspan=2, sticky="ew", pady=5)
        headers_frame.columnconfigure(0, weight=1)
        row += 1
        
        # Cookies
        ttk.Label(headers_frame, text="Cookies (format: name=value, one per line):").grid(row=row, column=0, columnspan=2, sticky="w", pady=5)
        row += 1
        
        txt_cookies = ScrolledText(headers_frame, height=6, width=50)
        cookies_text = "\n".join([f"{k}={v}" for k, v in self.settings['cookies'].items()])
        txt_cookies.insert("1.0", cookies_text)
        txt_cookies.grid(row=row, column=0, columnspan=2, sticky="ew", pady=5)
        row += 1
        
        # Buttons
        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(fill="x", padx=10, pady=10)
        
        def save_settings():
            try:
                # General
                self.settings['user_agent'] = var_ua.get()
                self.settings['randomize_ua'] = var_randomize_ua.get()
                self.settings['respect_robots'] = var_respect_robots.get()
                self.settings['max_depth'] = int(var_max_depth.get())
                self.settings['max_retries'] = int(var_max_retries.get())
                self.settings['retry_backoff'] = float(var_backoff.get())
                self.settings['randomize_delay'] = var_randomize_delay.get()
                
                # Phase 3: Rate control
                delay_min_text = var_delay_min.get().strip()
                self.settings['delay_min'] = float(delay_min_text) if delay_min_text else None
                delay_max_text = var_delay_max.get().strip()
                self.settings['delay_max'] = float(delay_max_text) if delay_max_text else None
                
                # Phase 2: Screenshot per page
                self.screenshot_per_page = var_screenshot_per_page.get()
                self.screenshot_dir = var_screenshot_dir.get().strip() if var_screenshot_dir.get().strip() else None
                
                if self.screenshot_per_page and not self.screenshot_dir:
                    messagebox.showwarning(APP_TITLE, "Please select a directory to save page HTML files.")
                    return
                
                # Domains
                domains_text = txt_domains.get("1.0", "end-1c").strip()
                self.settings['allowed_domains'] = [d.strip() for d in domains_text.split("\n") if d.strip()]
                
                # Filters
                filters_text = txt_filters.get("1.0", "end-1c").strip()
                self.settings['link_filters'] = [f.strip() for f in filters_text.split("\n") if f.strip()]
                
                # Headers
                headers_text = txt_headers.get("1.0", "end-1c").strip()
                self.settings['custom_headers'] = {}
                for line in headers_text.split("\n"):
                    line = line.strip()
                    if ":" in line:
                        key, value = line.split(":", 1)
                        self.settings['custom_headers'][key.strip()] = value.strip()
                
                # Cookies
                cookies_text = txt_cookies.get("1.0", "end-1c").strip()
                self.settings['cookies'] = {}
                for line in cookies_text.split("\n"):
                    line = line.strip()
                    if "=" in line:
                        key, value = line.split("=", 1)
                        self.settings['cookies'][key.strip()] = value.strip()
                
                # Phase 3: Proxies
                proxies_text = txt_proxies.get("1.0", "end-1c").strip()
                self.settings['proxies'] = [p.strip() for p in proxies_text.split("\n") if p.strip()]
                
                # Phase 3: Keywords
                include_keywords_text = txt_include_keywords.get("1.0", "end-1c").strip()
                self.settings['include_keywords'] = [k.strip() for k in include_keywords_text.split("\n") if k.strip()]
                
                exclude_keywords_text = txt_exclude_keywords.get("1.0", "end-1c").strip()
                self.settings['exclude_keywords'] = [k.strip() for k in exclude_keywords_text.split("\n") if k.strip()]
                
                messagebox.showinfo(APP_TITLE, "Settings saved successfully!")
                dialog.destroy()
            except Exception as e:
                messagebox.showerror(APP_TITLE, f"Error saving settings: {e}")
        
        ttk.Button(btn_frame, text="Save", command=save_settings).pack(side="right", padx=5)
        ttk.Button(btn_frame, text="Cancel", command=dialog.destroy).pack(side="right", padx=5)
    
    def on_open_in_browser(self):
        """Open current page in browser"""
        if self.current_url:
            webbrowser.open(self.current_url)

    def on_export_xml(self):
        if not self.rows:
            messagebox.showinfo(APP_TITLE, "No data to export.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".xml", filetypes=[("XML", "*.xml")], title="Save sitemap.xml")
        if not path:
            return
        lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
        for r in self.rows:
            lines.append("  <url>")
            lines.append(f"    <loc>{r.url}</loc>")
            lines.append("  </url>")
        lines.append("</urlset>")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        log_safe(self.log, f"Exported XML: {path}")

    def on_export_html(self):
        if not self.rows:
            messagebox.showinfo(APP_TITLE, "No data to export.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".html", filetypes=[("HTML", "*.html")], title="Save sitemap.html")
        if not path:
            return
        html = [
            "<!doctype html><meta charset='utf-8'><title>Sitemap</title>",
            "<style>body{font-family:system-ui,Segoe UI,Arial} table{border-collapse:collapse} td,th{border:1px solid #ccc;padding:6px 10px}</style>",
            "<h1>Sitemap</h1>",
            "<table><thead><tr><th>Title</th><th>Category</th><th>URL</th></tr></thead><tbody>",
        ]
        for r in self.rows:
            html.append(f"<tr><td>{r.title}</td><td>{r.category}</td><td><a href='{r.url}'>{r.url}</a></td></tr>")
        html.append("</tbody></table>")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(html))
        log_safe(self.log, f"Exported HTML: {path}")

    def on_save_tab_text(self):
        tab = self.nb.select()
        widget = self.nametowidget(tab)
        text = widget.get("1.0", "end-1c")
        path = filedialog.asksaveasfilename(defaultextension=".txt", filetypes=[("Text", "*.txt"), ("All", "*.*")])
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        log_safe(self.log, f"Saved text: {path}")

    def on_save_sitemap(self):
        if not self.rows:
            messagebox.showinfo(APP_TITLE, "Nothing to save yet.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")], title="Save Sitemap CSV")
        if not path:
            return
        import csv
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["Title", "Category", "URL"])
            for r in self.rows:
                w.writerow([r.title, r.category, r.url])
        log_safe(self.log, f"Saved CSV: {path}")

    def on_load_sitemap(self):
        path = filedialog.askopenfilename(filetypes=[("CSV", "*.csv")], title="Load Sitemap CSV")
        if not path:
            return
        import csv
        self.rows.clear()
        self.tree_item_ids.clear()
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        with open(path, newline="", encoding="utf-8") as f:
            rdr = csv.DictReader(f)
            for idx, row in enumerate(rdr):
                r = Row(title=row.get("Title",""), category=row.get("Category",""), url=row.get("URL",""))
                self.rows.append(r)
        # Refresh tree with all rows
        self._refresh_tree()
        log_safe(self.log, f"Loaded CSV: {path}")

    def on_clear(self):
        """Clear the list and start from scratch"""
        # Stop any running crawl
        if self.spider:
            self.spider.cancel()
        
        # Clear treeview
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        
        # Clear rows
        self.rows.clear()
        self.tree_item_ids.clear()
        
        # Reset progress bar
        self.var_pages_scraped.set("Pages scraped: 0")
        self.progress_bar['value'] = 0
        self.var_filter.set("")  # Clear filter
        
        # Clear previews
        self.txt_text.delete("1.0", "end")
        self.txt_xml.delete("1.0", "end")
        self.txt_html.delete("1.0", "end")
        self.txt_css.delete("1.0", "end")
        self.txt_js.delete("1.0", "end")
        self.txt_metadata.delete("1.0", "end")
        self.txt_tables.delete("1.0", "end")
        self.txt_json.configure(state="normal")
        self.txt_json.delete("1.0", "end")
        self.txt_json.configure(state="disabled")
        self.txt_regex.delete("1.0", "end")
        
        # Reset state
        self.current_html = ""
        self.current_url = ""
        self.var_page_title.set("Page Selected Title")
        self.var_status.set("Ready")
        
        # Reset sort
        self.sort_column = None
        self.sort_reverse = False
        for col in ["title", "category", "url"]:
            self.tree.heading(col, text=col.capitalize())
        
        # Clear scraped images
        self.scraped_images.clear()
        self.image_checkboxes.clear()
        self._clear_images_display()
        self.var_images_count.set("No images scraped")
        
        log_safe(self.log, "Cleared all data")
    
    def _clear_images_display(self):
        """Clear all images from the display"""
        for widget in self.images_container.winfo_children():
            widget.destroy()
    
    def on_scrape_images(self):
        """Scrape images from the current page"""
        if not self.current_html or not self.current_url:
            messagebox.showinfo(APP_TITLE, "No page loaded. Please select a page first.")
            return
        
        log_safe(self.log, f"Scraping images from {self.current_url}...")
        self.var_status.set("Scraping images...")
        
        # Extract images in a thread to avoid blocking UI
        def scrape_thread():
            try:
                images = self._extract_images(self.current_html, self.current_url)
                self.after(0, lambda: self._display_images(images))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror(APP_TITLE, f"Error scraping images: {e}"))
                self.after(0, lambda: self.var_status.set("Ready"))
        
        threading.Thread(target=scrape_thread, daemon=True).start()
    
    def _extract_images(self, html: str, base_url: str) -> List[Dict[str, Any]]:
        """Extract all images from HTML"""
        soup = BeautifulSoup(html, "lxml")
        images = []
        
        # Find all img tags
        img_tags = soup.find_all('img')
        
        for img in img_tags:
            src = img.get('src') or img.get('data-src') or img.get('data-lazy-src')
            if not src:
                continue
            
            # Resolve relative URLs
            img_url = urlparse.urljoin(base_url, src)
            
            # Skip data URIs (already embedded)
            if img_url.startswith('data:'):
                continue
            
            try:
                # Download the image
                response = requests.get(img_url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT}, stream=True)
                response.raise_for_status()
                
                # Get image data
                img_data = response.content
                
                # Get filename from URL
                parsed_url = urlparse.urlparse(img_url)
                filename = os.path.basename(parsed_url.path)
                if not filename or '.' not in filename:
                    # Generate filename from URL
                    filename = f"image_{len(images) + 1}.jpg"
                
                # Get image dimensions if possible
                try:
                    from PIL import Image
                    import io
                    img_obj = Image.open(io.BytesIO(img_data))
                    width, height = img_obj.size
                except:
                    width, height = None, None
                
                images.append({
                    'url': img_url,
                    'data': img_data,
                    'filename': filename,
                    'width': width,
                    'height': height,
                    'size': len(img_data)
                })
                
            except Exception as e:
                log_safe(self.log, f"Failed to download image {img_url}: {e}")
                continue
        
        return images
    
    def _display_images(self, images: List[Dict[str, Any]]):
        """Display scraped images in the container"""
        # Clear existing images
        self._clear_images_display()
        self.scraped_images.clear()
        self.image_checkboxes.clear()
        
        if not images:
            self.var_images_count.set("No images found")
            self.var_status.set("Ready")
            log_safe(self.log, "No images found on this page")
            return
        
        # Display images in a grid
        row = 0
        col = 0
        max_cols = 4  # 4 images per row
        
        for img_info in images:
            # Create frame for each image
            img_frame = ttk.Frame(self.images_container, relief="raised", borderwidth=1)
            img_frame.grid(row=row, column=col, padx=5, pady=5, sticky="nsew")
            
            # Checkbox for selection
            checkbox_var = tk.BooleanVar(value=True)
            self.image_checkboxes.append(checkbox_var)
            checkbox = ttk.Checkbutton(img_frame, variable=checkbox_var)
            checkbox.pack(anchor="w", padx=5, pady=2)
            
            # Display thumbnail
            try:
                from PIL import Image, ImageTk
                import io
                
                # Create thumbnail
                img_obj = Image.open(io.BytesIO(img_info['data']))
                img_obj.thumbnail((200, 200), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(img_obj)
                
                # Label to display image
                img_label = ttk.Label(img_frame, image=photo)
                img_label.image = photo  # Keep a reference
                img_label.pack(padx=5, pady=5)
                
            except Exception as e:
                # Fallback: show text if image can't be displayed
                ttk.Label(img_frame, text=f"Image\n{img_info['filename']}", 
                         wraplength=150).pack(padx=5, pady=5)
            
            # Image info
            info_text = f"{img_info['filename']}\n"
            if img_info['width'] and img_info['height']:
                info_text += f"{img_info['width']}x{img_info['height']}\n"
            size_kb = img_info['size'] / 1024
            info_text += f"{size_kb:.1f} KB"
            
            info_label = ttk.Label(img_frame, text=info_text, font=("Consolas", 8), 
                                  wraplength=180, justify="center")
            info_label.pack(padx=5, pady=2)
            
            # Store image info with checkbox
            img_info['checkbox_var'] = checkbox_var
            self.scraped_images.append(img_info)
            
            # Update grid position
            col += 1
            if col >= max_cols:
                col = 0
                row += 1
        
        self.var_images_count.set(f"{len(images)} image(s) scraped")
        self.var_status.set("Ready")
        log_safe(self.log, f"Scraped {len(images)} image(s)")
    
    def on_save_all_images(self):
        """Save all scraped images"""
        if not self.scraped_images:
            messagebox.showinfo(APP_TITLE, "No images to save.")
            return
        
        # Ask for directory
        save_dir = filedialog.askdirectory(title="Select folder to save images")
        if not save_dir:
            return
        
        saved_count = 0
        for img_info in self.scraped_images:
            try:
                filepath = os.path.join(save_dir, img_info['filename'])
                # Handle duplicate filenames
                counter = 1
                base_name, ext = os.path.splitext(img_info['filename'])
                while os.path.exists(filepath):
                    filepath = os.path.join(save_dir, f"{base_name}_{counter}{ext}")
                    counter += 1
                
                with open(filepath, 'wb') as f:
                    f.write(img_info['data'])
                saved_count += 1
            except Exception as e:
                log_safe(self.log, f"Failed to save {img_info['filename']}: {e}")
        
        messagebox.showinfo(APP_TITLE, f"Saved {saved_count} of {len(self.scraped_images)} image(s) to {save_dir}")
        log_safe(self.log, f"Saved {saved_count} image(s) to {save_dir}")
    
    def on_save_selected_images(self):
        """Save selected images"""
        if not self.scraped_images:
            messagebox.showinfo(APP_TITLE, "No images to save.")
            return
        
        # Get selected images
        selected_images = [img for img, checkbox_var in zip(self.scraped_images, self.image_checkboxes) 
                         if checkbox_var.get()]
        
        if not selected_images:
            messagebox.showinfo(APP_TITLE, "No images selected.")
            return
        
        # Ask for directory
        save_dir = filedialog.askdirectory(title="Select folder to save selected images")
        if not save_dir:
            return
        
        saved_count = 0
        for img_info in selected_images:
            try:
                filepath = os.path.join(save_dir, img_info['filename'])
                # Handle duplicate filenames
                counter = 1
                base_name, ext = os.path.splitext(img_info['filename'])
                while os.path.exists(filepath):
                    filepath = os.path.join(save_dir, f"{base_name}_{counter}{ext}")
                    counter += 1
                
                with open(filepath, 'wb') as f:
                    f.write(img_info['data'])
                saved_count += 1
            except Exception as e:
                log_safe(self.log, f"Failed to save {img_info['filename']}: {e}")
        
        messagebox.showinfo(APP_TITLE, f"Saved {saved_count} of {len(selected_images)} selected image(s) to {save_dir}")
        log_safe(self.log, f"Saved {saved_count} selected image(s) to {save_dir}")

    def on_screenshot(self):
        if ImageGrab is None:
            messagebox.showinfo(APP_TITLE, "Install Pillow for screenshots: pip install pillow")
            return
        # Grab the notebook area (current tab content)
        self.update_idletasks()
        
        # Get the notebook widget position
        nb_x = self.nb.winfo_rootx()
        nb_y = self.nb.winfo_rooty()
        nb_w = self.nb.winfo_width()
        nb_h = self.nb.winfo_height()
        
        # Capture the notebook area
        img = ImageGrab.grab(bbox=(nb_x, nb_y, nb_x + nb_w, nb_y + nb_h))
        
        path = filedialog.asksaveasfilename(defaultextension=".png", filetypes=[("PNG", "*.png")], title="Save Screenshot")
        if not path:
            return
        img.save(path)
        log_safe(self.log, f"Saved screenshot: {path}")

if __name__ == "__main__":
    try:
        App().mainloop()
    except KeyboardInterrupt:
        pass
