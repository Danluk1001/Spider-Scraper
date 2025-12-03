# Spider Scraper 🕷️

Spider Scraper is a desktop sitemap + web scraping tool built in **Python** with a Tkinter GUI.

It lets you:
- Crawl a website and build a sitemap
- View page text, HTML, XML, CSS, JS, metadata, tables, JSON and regex search results
- Edit categories for single or multiple pages
- Export data as XML / HTML
- Screenshot selected pages from inside the app
- Track crawl progress with a live progress bar and page counter
- See detailed logs of every request

## Screenshots

<img width="1920" height="1032" alt="Screenshot 2025-12-03 134831" src="https://github.com/user-attachments/assets/3c7ffdbd-b18c-4fb9-9722-8ef27a12f57d" />


## Tech Stack

- Python 3.x
- Tkinter (GUI)
- `requests` (HTTP client)
- `beautifulsoup4` + `lxml` (HTML parsing)
- `Pillow` (screenshots)

## Installation

```bash
git clone https://github.com/danluk1001/spider-scraper.git
cd spider-scraper
pip install -r requirements.txt
python SpiderScraper.py
