import os
import time
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, jsonify, send_from_directory
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup
import threading
import sqlite3
from concurrent.futures import ThreadPoolExecutor
import hashlib
import validators
from urllib.parse import urlparse, parse_qs, urlencode

app = Flask(__name__)

# Configuration
URLS_FILE = 'urls.txt'
DATA_FILE = 'url_data.db'
MAX_WORKERS = 10

# Create directories if they don't exist
os.makedirs('static', exist_ok=True)
os.makedirs('templates', exist_ok=True)

# Database setup with improved schema
def init_db():
    conn = sqlite3.connect(DATA_FILE)
    c = conn.cursor()
    
    # Main URLs table
    c.execute('''CREATE TABLE IF NOT EXISTS urls
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  url TEXT UNIQUE,
                  title TEXT,
                  description TEXT,
                  domain TEXT,
                  rating REAL DEFAULT 0,
                  clicks INTEGER DEFAULT 0,
                  created_at REAL,
                  last_updated REAL,
                  category TEXT,
                  tags TEXT)''')
    
    # Categories table
    c.execute('''CREATE TABLE IF NOT EXISTS categories
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  name TEXT UNIQUE,
                  description TEXT)''')
    
    # Popular domains table
    c.execute('''CREATE TABLE IF NOT EXISTS popular_domains
                 (domain TEXT PRIMARY KEY,
                  count INTEGER DEFAULT 1)''')
    
    # Create indexes for better performance
    c.execute('''CREATE INDEX IF NOT EXISTS idx_urls_domain ON urls(domain)''')
    c.execute('''CREATE INDEX IF NOT EXISTS idx_urls_category ON urls(category)''')
    c.execute('''CREATE INDEX IF NOT EXISTS idx_urls_rating ON urls(rating)''')
    
    # Insert default categories if they don't exist
    default_categories = [
        ('Technology', 'Tech websites and resources'),
        ('Education', 'Educational websites'),
        ('Entertainment', 'Entertainment and media'),
        ('Business', 'Business and finance'),
        ('News', 'News and journalism'),
        ('Shopping', 'E-commerce and shopping'),
        ('Social', 'Social media platforms')
    ]
    
    c.executemany('''INSERT OR IGNORE INTO categories (name, description) VALUES (?, ?)''', default_categories)
    
    conn.commit()
    conn.close()

init_db()

def is_duplicate_url(url):
    """Check if URL already exists in database or processing queue"""
    try:
        # Basic normalization
        url = url.strip().lower()
        if not url.startswith(('http://', 'https://')):
            url = 'http://' + url
        url = url.rstrip('/')
        
        # Check in database
        conn = sqlite3.connect(DATA_FILE)
        c = conn.cursor()
        c.execute("SELECT 1 FROM urls WHERE url = ?", (url,))
        if c.fetchone():
            conn.close()
            return True
        conn.close()
        
        # Check in processing queue
        if os.path.exists(URLS_FILE):
            with open(URLS_FILE, 'r') as f:
                for line in f:
                    if line.strip() and line.strip().lower() == url:
                        return True
        return False
        
    except Exception as e:
        print(f"Error checking duplicate URL: {str(e)}")
        return False

# Thread pool for URL processing
executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

def process_url(url):
    """Process a single URL and extract metadata"""
    try:
        # Validate URL format
        if not validators.url(url):
            if not url.startswith(('http://', 'https://')):
                url = 'http://' + url
            if not validators.url(url):
                return False
                
        parsed = urlparse(url)
        domain = parsed.netloc
        
        # Check if URL already exists
        conn = sqlite3.connect(DATA_FILE)
        c = conn.cursor()
        c.execute("SELECT 1 FROM urls WHERE url = ?", (url,))
        exists = c.fetchone()
        
        if exists:
            conn.close()
            return False
            
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        # Fetch the page
        response = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Extract metadata
        title = soup.title.string if soup.title else url
        description = ""
        category = None
        tags = []
        
        # Get description
        meta_desc = soup.find('meta', attrs={'name': 'description'}) or \
                   soup.find('meta', attrs={'property': 'og:description'})
        if meta_desc:
            description = meta_desc.get('content', '')
                
        # Auto-detect category based on domain
        domain_parts = domain.split('.')
        if len(domain_parts) > 1:
            main_domain = domain_parts[-2]
            
            # Simple domain to category mapping
            domain_categories = {
                'news': 'News',
                'tech': 'Technology',
                'edu': 'Education',
                'shop': 'Shopping',
                'blog': 'Education',
                'social': 'Social'
            }
            
            for key, cat in domain_categories.items():
                if key in main_domain:
                    category = cat
                    break
                    
        # Auto-generate some tags
        if 'blog' in domain or 'blog' in title.lower():
            tags.append('blog')
        if 'forum' in domain or 'forum' in title.lower():
            tags.append('forum')
            
        tags = ','.join(tags[:5]) if tags else None
        
        timestamp = time.time()
        
        # Insert into database
        c.execute('''INSERT INTO urls 
                     (url, title, description, domain, created_at, last_updated, category, tags)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                  (url, title[:255], description[:500], domain, timestamp, timestamp, category, tags))
        
        # Update popular domains count
        c.execute('''INSERT OR REPLACE INTO popular_domains 
                     (domain, count) 
                     VALUES (?, COALESCE((SELECT count FROM popular_domains WHERE domain = ?), 0) + 1)''',
                  (domain, domain))
        
        conn.commit()
        conn.close()
        
        return True
        
    except Exception as e:
        print(f"Error processing {url}: {str(e)}")
        return False

def process_urls():
    """Process URLs from the queue file"""
    while True:
        if os.path.exists(URLS_FILE):
            try:
                # Read and clear the file
                with open(URLS_FILE, 'r') as f:
                    urls = list({line.strip() for line in f if line.strip()})  # Use set to remove duplicates
                open(URLS_FILE, 'w').close()
                
                # Process each URL concurrently
                futures = []
                for url in urls:
                    futures.append(executor.submit(process_url, url))
                
                # Wait for all tasks to complete
                for future in futures:
                    future.result()
                        
            except Exception as e:
                print(f"Error processing URLs: {str(e)}")
        
        time.sleep(5)

# Start background thread
thread = threading.Thread(target=process_urls)
thread.daemon = True
thread.start()

# Helper functions
def get_urls(limit=None, order_by='clicks', category=None, domain=None):
    conn = sqlite3.connect(DATA_FILE)
    c = conn.cursor()
    
    query = "SELECT * FROM urls WHERE 1=1"
    params = []
    
    if category:
        query += " AND category = ?"
        params.append(category)
        
    if domain:
        query += " AND domain LIKE ?"
        params.append(f"%{domain}%")
    
    if order_by == 'clicks':
        query += " ORDER BY clicks DESC, rating DESC"
    elif order_by == 'recent':
        query += " ORDER BY created_at DESC"
    elif order_by == 'rating':
        query += " ORDER BY rating DESC, clicks DESC"
    elif order_by == 'domain':
        query += " ORDER BY domain"
    
    if limit:
        query += f" LIMIT {limit}"
    
    c.execute(query, params)
    results = [dict(zip([column[0] for column in c.description], row)) for row in c.fetchall()]
    conn.close()
    return results

def search_urls(query, category=None, domain=None):
    conn = sqlite3.connect(DATA_FILE)
    c = conn.cursor()
    
    query_str = f"%{query.lower()}%"
    
    sql = '''SELECT * FROM urls 
             WHERE (LOWER(title) LIKE ? OR 
                   LOWER(description) LIKE ? OR 
                   LOWER(domain) LIKE ? OR
                   LOWER(tags) LIKE ?)'''
    
    params = [query_str, query_str, query_str, query_str]
    
    if category:
        sql += " AND category = ?"
        params.append(category)
        
    if domain:
        sql += " AND domain LIKE ?"
        params.append(f"%{domain}%")
    
    sql += " ORDER BY clicks DESC, rating DESC"
    
    c.execute(sql, params)
    results = [dict(zip([column[0] for column in c.description], row)) for row in c.fetchall()]
    conn.close()
    return results

def get_paginated_urls(page=1, per_page=50, category=None, domain=None):
    conn = sqlite3.connect(DATA_FILE)
    c = conn.cursor()
    
    # Get total count
    count_query = "SELECT COUNT(*) FROM urls WHERE 1=1"
    count_params = []
    
    if category:
        count_query += " AND category = ?"
        count_params.append(category)
        
    if domain:
        count_query += " AND domain LIKE ?"
        count_params.append(f"%{domain}%")
    
    c.execute(count_query, count_params)
    total = c.fetchone()[0]
    
    # Get paginated results
    offset = (page - 1) * per_page
    query = "SELECT * FROM urls WHERE 1=1"
    params = []
    
    if category:
        query += " AND category = ?"
        params.append(category)
        
    if domain:
        query += " AND domain LIKE ?"
        params.append(f"%{domain}%")
    
    query += " ORDER BY clicks DESC, rating DESC LIMIT ? OFFSET ?"
    params.extend([per_page, offset])
    
    c.execute(query, params)
    results = [dict(zip([column[0] for column in c.description], row)) for row in c.fetchall()]
    conn.close()
    
    return {
        'urls': results,
        'total': total,
        'page': page,
        'per_page': per_page,
        'total_pages': (total + per_page - 1) // per_page
    }

def get_categories():
    conn = sqlite3.connect(DATA_FILE)
    c = conn.cursor()
    c.execute("SELECT name, description FROM categories ORDER BY name")
    results = [dict(zip([column[0] for column in c.description], row)) for row in c.fetchall()]
    conn.close()
    return results

def get_popular_domains(limit=10):
    conn = sqlite3.connect(DATA_FILE)
    c = conn.cursor()
    c.execute("SELECT domain, count FROM popular_domains ORDER BY count DESC LIMIT ?", (limit,))
    results = [dict(zip([column[0] for column in c.description], row)) for row in c.fetchall()]
    conn.close()
    return results

# Routes
@app.route('/')
def home():
    top_urls = get_urls(limit=12, order_by='clicks')
    recent_urls = get_urls(limit=12, order_by='recent')
    popular_domains = get_popular_domains()
    categories = get_categories()
    
    conn = sqlite3.connect(DATA_FILE)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM urls")
    total_urls = c.fetchone()[0]
    conn.close()
    
    return render_template('index.html', 
                         top_urls=top_urls, 
                         recent_urls=recent_urls,
                         total_urls=total_urls,
                         popular_domains=popular_domains,
                         categories=categories)

@app.route('/add', methods=['POST'])
def add_url():
    url = request.form.get('url', '').strip()
    if not url:
        return jsonify({'success': False, 'message': 'Please provide a URL'}), 400
    
    try:
        # Validate URL format
        if not validators.url(url):
            if not url.startswith(('http://', 'https://')):
                url = 'http://' + url
            if not validators.url(url):
                return jsonify({'success': False, 'message': 'Invalid URL format'}), 400
        
        # Check for duplicates
        if is_duplicate_url(url):
            return jsonify({'success': False, 'message': 'This URL already exists or is in processing queue'}), 400
        
        # Add to queue
        with open(URLS_FILE, 'a') as f:
            f.write(url + '\n')
        
        return jsonify({'success': True, 'message': 'URL added to processing queue!'})
    
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500
    
    return jsonify({'success': False, 'message': 'Please provide a URL'}), 400

@app.route('/search')
def search():
    query = request.args.get('q', '').lower().strip()
    category = request.args.get('category', '')
    domain = request.args.get('domain', '')
    
    if not query and not category and not domain:
        return redirect(url_for('home'))
    
    results = search_urls(query, category if category != 'all' else None, domain if domain != 'all' else None)
    categories = get_categories()
    popular_domains = get_popular_domains()
    
    return render_template('search.html', 
                         query=query,
                         category=category,
                         domain=domain,
                         results=results,
                         categories=categories,
                         popular_domains=popular_domains)

@app.route('/click', methods=['POST'])
def track_click():
    url = request.json.get('url')
    if url:
        conn = sqlite3.connect(DATA_FILE)
        c = conn.cursor()
        c.execute("UPDATE urls SET clicks = clicks + 1 WHERE url = ?", (url,))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    return jsonify({'success': False}), 404

@app.route('/rate', methods=['POST'])
def rate_url():
    url = request.json.get('url')
    rating = request.json.get('rating')
    
    if url and rating in (1, 2, 3, 4, 5):
        conn = sqlite3.connect(DATA_FILE)
        c = conn.cursor()
        
        # Get current rating to calculate new average
        c.execute("SELECT rating FROM urls WHERE url = ?", (url,))
        current_rating = c.fetchone()[0]
        
        if current_rating == 0:
            new_rating = rating
        else:
            new_rating = round((current_rating + rating) / 2, 1)
        
        c.execute("UPDATE urls SET rating = ? WHERE url = ?", (new_rating, url))
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'new_rating': new_rating})
    return jsonify({'success': False}), 400

@app.route('/all')
def all_urls():
    try:
        # Get and validate page number
        page = request.args.get('page', '1')
        if not page.isdigit() or int(page) < 1:
            page = 1
        else:
            page = int(page)
        
        category = request.args.get('category', 'all')
        domain = request.args.get('domain', 'all')
        
        # Get paginated data
        try:
            data = get_paginated_urls(
                page=page, 
                category=category if category != 'all' else None,
                domain=domain if domain != 'all' else None
            )
        except Exception as e:
            print(f"Error getting paginated URLs: {str(e)}")
            return render_template('error.html', message="Could not load URLs. Please try again later."), 500
        
        # Get categories and popular domains
        try:
            categories = get_categories()
            popular_domains = get_popular_domains()
        except Exception as e:
            print(f"Error getting categories/domains: {str(e)}")
            categories = []
            popular_domains = []
        
        return render_template('all.html', 
                            urls=data.get('urls', []), 
                            page=data.get('page', 1), 
                            total_pages=data.get('total_pages', 1),
                            total=data.get('total', 0),
                            category=category,
                            domain=domain,
                            categories=categories,
                            popular_domains=popular_domains)
    
    except Exception as e:
        print(f"Unexpected error in all_urls: {str(e)}")
        return render_template('error.html', message="An unexpected error occurred."), 500

def get_paginated_urls(page=1, per_page=50, category=None, domain=None):
    conn = None
    try:
        conn = sqlite3.connect(DATA_FILE)
        c = conn.cursor()
        
        # Build base queries
        base_query = "FROM urls WHERE 1=1"
        params = []
        
        if category:
            base_query += " AND category = ?"
            params.append(category)
            
        if domain:
            base_query += " AND domain LIKE ?"
            params.append(f"%{domain}%")
        
        # Get total count
        count_query = f"SELECT COUNT(*) {base_query}"
        c.execute(count_query, params)
        total = c.fetchone()[0]
        
        # Calculate total pages
        total_pages = max(1, (total + per_page - 1) // per_page)
        
        # Validate page number
        page = max(1, min(page, total_pages))
        
        # Get paginated results
        offset = (page - 1) * per_page
        query = f"SELECT * {base_query} ORDER BY clicks DESC, rating DESC LIMIT ? OFFSET ?"
        params.extend([per_page, offset])
        
        c.execute(query, params)
        results = [dict(zip([column[0] for column in c.description], row)) for row in c.fetchall()]
        
        return {
            'urls': results,
            'total': total,
            'page': page,
            'per_page': per_page,
            'total_pages': total_pages
        }
        
    except Exception as e:
        print(f"Error in get_paginated_urls: {str(e)}")
        raise
    finally:
        if conn:
            conn.close()

@app.route('/category/<category_name>')
def category_view(category_name):
    page = int(request.args.get('page', 1))
    top_urls = get_urls(limit=12, order_by='clicks', category=category_name)
    recent_urls = get_urls(limit=12, order_by='recent', category=category_name)
    
    conn = sqlite3.connect(DATA_FILE)
    c = conn.cursor()
    c.execute("SELECT description FROM categories WHERE name = ?", (category_name,))
    category_desc = c.fetchone()
    category_desc = category_desc[0] if category_desc else ""
    
    c.execute("SELECT COUNT(*) FROM urls WHERE category = ?", (category_name,))
    total_urls = c.fetchone()[0]
    conn.close()
    
    return render_template('category.html', 
                         category_name=category_name,
                         category_desc=category_desc,
                         top_urls=top_urls,
                         recent_urls=recent_urls,
                         total_urls=total_urls)

@app.route('/domain/<domain_name>')
def domain_view(domain_name):
    page = int(request.args.get('page', 1))
    urls = get_urls(limit=50, order_by='clicks', domain=domain_name)
    
    conn = sqlite3.connect(DATA_FILE)
    c = conn.cursor()
    c.execute("SELECT count FROM popular_domains WHERE domain = ?", (domain_name,))
    domain_count = c.fetchone()
    domain_count = domain_count[0] if domain_count else 0
    conn.close()
    
    return render_template('domain.html', 
                         domain_name=domain_name,
                         domain_count=domain_count,
                         urls=urls)

# Template filters
@app.template_filter('domain')
def domain_filter(url):
    return urlparse(url).netloc

@app.template_filter('time')
def time_filter(timestamp):
    return datetime.fromtimestamp(timestamp).strftime('%b %d, %Y')

@app.template_filter('shorten')
def shorten_filter(text, length=100):
    if len(text) > length:
        return text[:length] + '...'
    return text

# Create templates if they don't exist
template_dir = os.path.join(os.path.dirname(__file__), 'templates')

# Index template
index_template = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Yamajodo</title>
      <link rel="icon" href="/static/icon.ico" type="image/x-icon">
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0-beta3/css/all.min.css">
    <style>
        .star-rating {
            color: #fbbf24;
        }
        .url-item {
            transition: all 0.2s;
            cursor: pointer;
        }
        .url-item:hover {
            transform: translateY(-2px);
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
        }
        .hidden-content {
            display: none;
        }
        #addUrlForm {
            display: none;
        }
        #addUrlForm.show {
            display: block;
        }
        .logo-title {
  display: flex;
  align-items: center;
  font-size: 2.25rem; /* Tailwind's text-4xl equivalent */
  font-weight: bold;
  color: #1e3a8a; /* Tailwind's text-blue-800 equivalent */
  margin-bottom: 0.5rem; /* Tailwind's mb-2 equivalent */
}

.logo-img {
  width: 1.5em; /* Adjust size as needed */
  height: auto;
  margin-right: 0.5rem; /* Space between logo and text */
}

    </style>
</head>
<body class="bg-gray-50">
    <div class="container mx-auto px-4 py-8">
        <!-- Header -->
        <header class="text-center mb-12">
            <h1 class="logo-title">
  <img src="/static/icon.ico" alt="Yamajodo Logo" class="logo-img">
  Yamajodo
</h1>

            <p class="text-gray-600">Total websites: <span class="font-semibold">{{ "{:,}".format(total_urls) }}</span></p>
        </header>
        
        <!-- Search Box -->
        <div class="max-w-2xl mx-auto mb-10">
            <form action="/search" method="get" class="flex">
                <input type="text" name="q" placeholder="Search websites..." 
                       class="flex-grow px-4 py-2 border border-gray-300 rounded-l-lg focus:outline-none focus:ring-2 focus:ring-blue-500">
                <button type="submit" class="bg-blue-600 text-white px-6 py-2 rounded-r-lg hover:bg-blue-700 transition">
                    <i class="fas fa-search"></i> Search
                </button>
            </form>
        </div>
        
        <!-- Toggle Buttons -->
        <div class="flex justify-center gap-4 mb-8">
            <button onclick="toggleVisibility('categories')" class="px-4 py-2 bg-blue-100 text-blue-800 rounded-lg hover:bg-blue-200 transition">
                <i class="fas fa-folder mr-2"></i>Toggle Categories
            </button>
            <button onclick="toggleVisibility('domains')" class="px-4 py-2 bg-blue-100 text-blue-800 rounded-lg hover:bg-blue-200 transition">
                <i class="fas fa-globe mr-2"></i>Toggle Domains
            </button>
            <button onclick="toggleVisibility('addUrlForm')" class="px-4 py-2 bg-green-100 text-green-800 rounded-lg hover:bg-green-200 transition">
                <i class="fas fa-plus mr-2"></i>Add URL
            </button>
        </div>
        
        <!-- Add URL Form (Hidden by default) -->
        <div id="addUrlForm" class="max-w-2xl mx-auto mb-12 bg-white p-6 rounded-lg shadow-md hidden-content">
            <h2 class="text-xl font-semibold mb-4 text-gray-800">Add a Website</h2>
            <form id="addForm" onsubmit="addUrl(event)" class="flex">
                <input type="text" id="urlInput" placeholder="https://example.com" required
                       class="flex-grow px-4 py-2 border border-gray-300 rounded-l-lg focus:outline-none focus:ring-2 focus:ring-blue-500">
                <button type="submit" class="bg-green-600 text-white px-6 py-2 rounded-r-lg hover:bg-green-700 transition">
                    <i class="fas fa-plus"></i> Add
                </button>
            </form>
        </div>
        
        <!-- Categories Quick Links (Hidden by default) -->
        <div id="categories" class="hidden-content mb-12">
            <h2 class="text-2xl font-semibold mb-6 text-gray-800 border-b pb-2">Categories</h2>
            <div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-7 gap-4">
                {% for category in categories %}
                <a href="/category/{{ category.name }}" class="bg-white p-4 rounded-lg shadow-sm hover:shadow-md transition text-center">
                    <div class="text-blue-600 mb-2">
                        <i class="fas fa-folder-open text-2xl"></i>
                    </div>
                    <h3 class="font-medium text-gray-800">{{ category.name }}</h3>
                </a>
                {% endfor %}
            </div>
        </div>
        
        <!-- Popular Domains (Hidden by default) -->
        <div id="domains" class="hidden-content mb-12">
            <h2 class="text-2xl font-semibold mb-6 text-gray-800 border-b pb-2">Popular Domains</h2>
            <div class="flex flex-wrap gap-2">
                {% for domain in popular_domains %}
                <a href="/domain/{{ domain.domain }}" class="px-4 py-2 bg-white rounded-lg hover:bg-gray-100 transition flex items-center">
                    <span class="text-gray-800">{{ domain.domain }}</span>
                    <span class="ml-2 bg-blue-100 text-blue-800 text-xs px-2 py-1 rounded-full">{{ domain.count }}</span>
                </a>
                {% endfor %}
            </div>
        </div>
        
        <!-- Popular Websites -->
        <div class="mb-12">
            <h2 class="text-2xl font-semibold mb-6 text-gray-800 border-b pb-2 flex justify-between items-center">
                <span>Popular Websites</span>
                <a href="/all?page=1" class="text-sm font-normal text-blue-600 hover:underline">View All</a>
            </h2>
            <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                {% for url in top_urls %}
                <div class="url-item bg-white rounded-lg p-4 shadow-sm hover:shadow-md transition" onclick="window.open('{{ url.url }}', '_blank'); trackClick('{{ url.url }}')">
                    <div class="flex items-start">
                        <div class="flex-1">
                            <h3 class="font-semibold text-lg mb-1">
                                {{ url.title|shorten(50) }}
                            </h3>
                            <div class="flex items-center mb-2">
                                <span class="text-sm text-gray-500">{{ url.domain|domain }}</span>
                                {% if url.category %}
                                <span class="ml-2 text-xs px-2 py-1 rounded bg-blue-100 text-blue-800">{{ url.category }}</span>
                                {% endif %}
                            </div>
                        </div>
                    </div>
                    <p class="text-gray-600 text-sm mb-3">{{ url.description|shorten(100) }}</p>
                    <div class="flex justify-between items-center">
                        <div class="star-rating" title="{{ url.rating }} stars" onclick="event.stopPropagation();">
                            {% for i in range(1, 6) %}
                                <span onclick="rateUrl('{{ url.url }}', {{ i }})" class="cursor-pointer">
                                    {{ '★' if i <= url.rating|round else '☆' }}
                                </span>
                            {% endfor %}
                        </div>
                        <span class="text-xs text-gray-500">{{ "{:,}".format(url.clicks) }} visits</span>
                    </div>
                </div>
                {% endfor %}
            </div>
        </div>
        
        <!-- Recently Added -->
        <div class="mb-12">
            <h2 class="text-2xl font-semibold mb-6 text-gray-800 border-b pb-2">Recently Added</h2>
            <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                {% for url in recent_urls %}
                <div class="url-item bg-white rounded-lg p-4 shadow-sm hover:shadow-md transition" onclick="window.open('{{ url.url }}', '_blank'); trackClick('{{ url.url }}')">
                    <div class="flex items-start">
                        <div class="flex-1">
                            <h3 class="font-semibold text-lg mb-1">
                                {{ url.title|shorten(50) }}
                            </h3>
                            <div class="flex items-center mb-2">
                                <span class="text-sm text-gray-500">{{ url.domain|domain }}</span>
                                {% if url.category %}
                                <span class="ml-2 text-xs px-2 py-1 rounded bg-blue-100 text-blue-800">{{ url.category }}</span>
                                {% endif %}
                            </div>
                        </div>
                    </div>
                    <p class="text-gray-600 text-sm mb-3">{{ url.description|shorten(100) }}</p>
                    <div class="flex justify-between items-center">
                        <div class="star-rating" title="{{ url.rating }} stars" onclick="event.stopPropagation();">
                            {% for i in range(1, 6) %}
                                <span onclick="rateUrl('{{ url.url }}', {{ i }})" class="cursor-pointer">
                                    {{ '★' if i <= url.rating|round else '☆' }}
                                </span>
                            {% endfor %}
                        </div>
                        <span class="text-xs text-gray-500">Added: {{ url.created_at|time }}</span>
                    </div>
                </div>
                {% endfor %}
            </div>
        </div>
    </div>
    
    <script>
        function addUrl(e) {
            e.preventDefault();
            const url = document.getElementById('urlInput').value.trim();
            if (!url) return;
            
            fetch('/add', {
                method: 'POST',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                body: `url=${encodeURIComponent(url)}`
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    alert('URL added successfully! It will be processed shortly.');
                    document.getElementById('urlInput').value = '';
                    setTimeout(() => location.reload(), 1500);
                } else {
                    alert('Error: ' + data.message);
                }
            })
            .catch(error => {
                alert('An error occurred: ' + error);
            });
        }
        
        function trackClick(url) {
            fetch('/click', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url })
            });
        }
        
        function rateUrl(url, rating) {
            fetch('/rate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url, rating })
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    location.reload();
                }
            });
        }
        
        function toggleVisibility(id) {
            const element = document.getElementById(id);
            if (element.style.display === 'none' || element.style.display === '') {
                element.style.display = 'block';
            } else {
                element.style.display = 'none';
            }
        }
    </script>
</body>
</html>
'''

# Search template
search_template = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Search Results for "{{ query }}"</title>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0-beta3/css/all.min.css">
    <style>
        .star-rating {
            color: #fbbf24;
        }
        .url-item {
            transition: all 0.2s;
            cursor: pointer;
        }
        .url-item:hover {
            transform: translateY(-2px);
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
        }
    </style>
</head>
<body class="bg-gray-50">
    <div class="container mx-auto px-4 py-8">
        <!-- Header -->
        <header class="text-center mb-12">
            <h1 class="text-3xl font-bold text-blue-800 mb-2">
                {% if query %}Search Results for "{{ query }}"{% else %}Browse Websites{% endif %}
            </h1>
            <p class="text-gray-600">{{ results|length }} results found</p>
        </header>
        
        <!-- Search Filters -->
        <div class="bg-white p-6 rounded-lg shadow-md mb-8">
            <form action="/search" method="get" class="space-y-4">
                <input type="hidden" name="q" value="{{ query }}">
                
                <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-1">Category</label>
                        <select name="category" class="w-full px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500">
                            <option value="all">All Categories</option>
                            {% for category in categories %}
                            <option value="{{ category.name }}" {% if category.name == category %}selected{% endif %}>{{ category.name }}</option>
                            {% endfor %}
                        </select>
                    </div>
                    
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-1">Domain</label>
                        <select name="domain" class="w-full px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500">
                            <option value="all">All Domains</option>
                            {% for domain in popular_domains %}
                            <option value="{{ domain.domain }}" {% if domain.domain == domain %}selected{% endif %}>{{ domain.domain }}</option>
                            {% endfor %}
                        </select>
                    </div>
                    
                    <div class="flex items-end">
                        <button type="submit" class="w-full bg-blue-600 text-white px-6 py-2 rounded-lg hover:bg-blue-700 transition">
                            <i class="fas fa-filter"></i> Apply Filters
                        </button>
                    </div>
                </div>
            </form>
        </div>
        
        <!-- Search Results -->
        {% if results %}
        <div class="space-y-4">
            {% for url in results %}
                        <div class="url-item bg-white rounded-lg p-4 shadow-sm hover:shadow-md transition" onclick="window.open('{{ url.url }}', '_blank'); trackClick('{{ url.url }}')">
                <div class="flex items-start">
                    <div class="flex-1">
                        <h3 class="font-semibold text-lg mb-1">
                            {{ url.title|shorten(50) }}
                        </h3>
                        <div class="flex items-center mb-2">
                            <span class="text-sm text-gray-500">{{ url.domain|domain }}</span>
                            {% if url.category %}
                            <span class="ml-2 text-xs px-2 py-1 rounded bg-blue-100 text-blue-800">{{ url.category }}</span>
                            {% endif %}
                        </div>
                    </div>
                </div>
                <p class="text-gray-600 text-sm mb-3">{{ url.description|shorten(100) }}</p>
                <div class="flex justify-between items-center">
                    <div class="star-rating" title="{{ url.rating }} stars" onclick="event.stopPropagation();">
                        {% for i in range(1, 6) %}
                            <span onclick="rateUrl('{{ url.url }}', {{ i }})" class="cursor-pointer">
                                {{ '★' if i <= url.rating|round else '☆' }}
                            </span>
                        {% endfor %}
                    </div>
                    <span class="text-xs text-gray-500">{{ "{:,}".format(url.clicks) }} visits</span>
                </div>
            </div>
            {% endfor %}
        </div>
        {% else %}
        <div class="bg-white p-8 rounded-lg shadow-md text-center">
            <i class="fas fa-search fa-3x text-gray-300 mb-4"></i>
            <h3 class="text-xl font-medium text-gray-700 mb-2">No results found</h3>
            <p class="text-gray-500">Try different search terms or filters</p>
        </div>
        {% endif %}
        
        <!-- Back to Home -->
        <div class="mt-8 text-center">
            <a href="/" class="inline-block bg-gray-200 text-gray-700 px-6 py-2 rounded-lg hover:bg-gray-300 transition">
                <i class="fas fa-arrow-left"></i> Back to Home
            </a>
        </div>
    </div>
    
    <script>
        function trackClick(url) {
            fetch('/click', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url })
            });
        }
        
        function rateUrl(url, rating) {
            fetch('/rate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url, rating })
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    location.reload();
                }
            });
        }
    </script>
</body>
</html>
'''

# All URLs template
all_template = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>All Websites - Page {{ page }}</title>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0-beta3/css/all.min.css">
    <style>
        .star-rating {
            color: #fbbf24;
        }
        .url-item {
            transition: all 0.2s;
            cursor: pointer;
        }
        .url-item:hover {
            transform: translateY(-2px);
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
        }
        .pagination .active {
            background-color: #3b82f6;
            color: white;
        }
    </style>
</head>
<body class="bg-gray-50">
    <div class="container mx-auto px-4 py-8">
        <!-- Header -->
        <header class="text-center mb-12">
            <h1 class="text-3xl font-bold text-blue-800 mb-2">All Websites</h1>
            <p class="text-gray-600">Page {{ page }} of {{ total_pages }} ({{ "{:,}".format(total) }} total)</p>
        </header>
        
        <!-- Search and Filters -->
        <div class="bg-white p-6 rounded-lg shadow-md mb-8">
            <form action="/all" method="get" class="space-y-4">
                <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-1">Category</label>
                        <select name="category" class="w-full px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500">
                            <option value="all">All Categories</option>
                            {% for category in categories %}
                            <option value="{{ category.name }}" {% if category.name == category %}selected{% endif %}>{{ category.name }}</option>
                            {% endfor %}
                        </select>
                    </div>
                    
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-1">Domain</label>
                        <select name="domain" class="w-full px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500">
                            <option value="all">All Domains</option>
                            {% for domain in popular_domains %}
                            <option value="{{ domain.domain }}" {% if domain.domain == domain %}selected{% endif %}>{{ domain.domain }}</option>
                            {% endfor %}
                        </select>
                    </div>
                    
                    <div class="flex items-end">
                        <button type="submit" class="w-full bg-blue-600 text-white px-6 py-2 rounded-lg hover:bg-blue-700 transition">
                            <i class="fas fa-filter"></i> Apply Filters
                        </button>
                    </div>
                </div>
            </form>
        </div>
        
        <!-- All URLs List -->
        <div class="space-y-4 mb-8">
            {% for url in urls %}
            <div class="url-item bg-white rounded-lg p-4 shadow-sm hover:shadow-md transition" onclick="window.open('{{ url.url }}', '_blank'); trackClick('{{ url.url }}')">
                <div class="flex items-start">
                    <div class="flex-1">
                        <h3 class="font-semibold text-lg mb-1">
                            {{ url.title|shorten(50) }}
                        </h3>
                        <div class="flex items-center mb-2">
                            <span class="text-sm text-gray-500">{{ url.domain|domain }}</span>
                            {% if url.category %}
                            <span class="ml-2 text-xs px-2 py-1 rounded bg-blue-100 text-blue-800">{{ url.category }}</span>
                            {% endif %}
                        </div>
                    </div>
                </div>
                <p class="text-gray-600 text-sm mb-3">{{ url.description|shorten(100) }}</p>
                <div class="flex justify-between items-center">
                    <div class="star-rating" title="{{ url.rating }} stars" onclick="event.stopPropagation();">
                        {% for i in range(1, 6) %}
                            <span onclick="rateUrl('{{ url.url }}', {{ i }})" class="cursor-pointer">
                                {{ '★' if i <= url.rating|round else '☆' }}
                            </span>
                        {% endfor %}
                    </div>
                    <span class="text-xs text-gray-500">{{ "{:,}".format(url.clicks) }} visits</span>
                </div>
            </div>
            {% endfor %}
        </div>
        
        <!-- Pagination -->
        <div class="flex justify-center mb-8">
            <div class="inline-flex rounded-md shadow-sm">
                {% if page > 1 %}
                <a href="/all?page={{ page - 1 }}{% if category != 'all' %}&category={{ category }}{% endif %}{% if domain != 'all' %}&domain={{ domain }}{% endif %}" 
                   class="px-4 py-2 border border-gray-300 rounded-l-lg hover:bg-gray-100 transition">
                    <i class="fas fa-chevron-left"></i> Previous
                </a>
                {% endif %}
                
                {% for p in range(1, total_pages + 1) %}
                    {% if p == page %}
                    <a href="/all?page={{ p }}{% if category != 'all' %}&category={{ category }}{% endif %}{% if domain != 'all' %}&domain={{ domain }}{% endif %}" 
                       class="px-4 py-2 border border-gray-300 bg-blue-600 text-white">
                        {{ p }}
                    </a>
                    {% else %}
                    <a href="/all?page={{ p }}{% if category != 'all' %}&category={{ category }}{% endif %}{% if domain != 'all' %}&domain={{ domain }}{% endif %}" 
                       class="px-4 py-2 border border-gray-300 hover:bg-gray-100 transition">
                        {{ p }}
                    </a>
                    {% endif %}
                {% endfor %}
                
                {% if page < total_pages %}
                <a href="/all?page={{ page + 1 }}{% if category != 'all' %}&category={{ category }}{% endif %}{% if domain != 'all' %}&domain={{ domain }}{% endif %}" 
                   class="px-4 py-2 border border-gray-300 rounded-r-lg hover:bg-gray-100 transition">
                    Next <i class="fas fa-chevron-right"></i>
                </a>
                {% endif %}
            </div>
        </div>
        
        <!-- Back to Home -->
        <div class="text-center">
            <a href="/" class="inline-block bg-gray-200 text-gray-700 px-6 py-2 rounded-lg hover:bg-gray-300 transition">
                <i class="fas fa-arrow-left"></i> Back to Home
            </a>
        </div>
    </div>
    
    <script>
        function trackClick(url) {
            fetch('/click', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url })
            });
        }
        
        function rateUrl(url, rating) {
            fetch('/rate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url, rating })
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    location.reload();
                }
            });
        }
    </script>
</body>
</html>
'''

# Category template
category_template = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ category_name }} - Web Directory</title>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0-beta3/css/all.min.css">
    <style>
        .star-rating {
            color: #fbbf24;
        }
        .url-item {
            transition: all 0.2s;
            cursor: pointer;
        }
        .url-item:hover {
            transform: translateY(-2px);
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
        }
    </style>
</head>
<body class="bg-gray-50">
    <div class="container mx-auto px-4 py-8">
        <!-- Header -->
        <header class="text-center mb-12">
            <h1 class="text-3xl font-bold text-blue-800 mb-2">{{ category_name }}</h1>
            <p class="text-gray-600">{{ category_desc }}</p>
            <p class="text-gray-600 mt-2">{{ "{:,}".format(total_urls) }} websites in this category</p>
        </header>
        
        <!-- Popular in Category -->
        <div class="mb-12">
            <h2 class="text-2xl font-semibold mb-6 text-gray-800 border-b pb-2">Popular in {{ category_name }}</h2>
            <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                {% for url in top_urls %}
                <div class="url-item bg-white rounded-lg p-4 shadow-sm hover:shadow-md transition" onclick="window.open('{{ url.url }}', '_blank'); trackClick('{{ url.url }}')">
                    <div class="flex items-start">
                        <div class="flex-1">
                            <h3 class="font-semibold text-lg mb-1">
                                {{ url.title|shorten(50) }}
                            </h3>
                            <div class="flex items-center mb-2">
                                <span class="text-sm text-gray-500">{{ url.domain|domain }}</span>
                            </div>
                        </div>
                    </div>
                    <p class="text-gray-600 text-sm mb-3">{{ url.description|shorten(100) }}</p>
                    <div class="flex justify-between items-center">
                        <div class="star-rating" title="{{ url.rating }} stars" onclick="event.stopPropagation();">
                            {% for i in range(1, 6) %}
                                <span onclick="rateUrl('{{ url.url }}', {{ i }})" class="cursor-pointer">
                                    {{ '★' if i <= url.rating|round else '☆' }}
                                </span>
                            {% endfor %}
                        </div>
                        <span class="text-xs text-gray-500">{{ "{:,}".format(url.clicks) }} visits</span>
                    </div>
                </div>
                {% endfor %}
            </div>
        </div>
        
        <!-- Recently Added in Category -->
        <div class="mb-12">
            <h2 class="text-2xl font-semibold mb-6 text-gray-800 border-b pb-2">Recently Added in {{ category_name }}</h2>
            <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                {% for url in recent_urls %}
                <div class="url-item bg-white rounded-lg p-4 shadow-sm hover:shadow-md transition" onclick="window.open('{{ url.url }}', '_blank'); trackClick('{{ url.url }}')">
                    <div class="flex items-start">
                        <div class="flex-1">
                            <h3 class="font-semibold text-lg mb-1">
                                {{ url.title|shorten(50) }}
                            </h3>
                            <div class="flex items-center mb-2">
                                <span class="text-sm text-gray-500">{{ url.domain|domain }}</span>
                            </div>
                        </div>
                    </div>
                    <p class="text-gray-600 text-sm mb-3">{{ url.description|shorten(100) }}</p>
                    <div class="flex justify-between items-center">
                        <div class="star-rating" title="{{ url.rating }} stars" onclick="event.stopPropagation();">
                            {% for i in range(1, 6) %}
                                <span onclick="rateUrl('{{ url.url }}', {{ i }})" class="cursor-pointer">
                                    {{ '★' if i <= url.rating|round else '☆' }}
                                </span>
                            {% endfor %}
                        </div>
                        <span class="text-xs text-gray-500">Added: {{ url.created_at|time }}</span>
                    </div>
                </div>
                {% endfor %}
            </div>
        </div>
        
        <!-- Back to Home -->
        <div class="text-center">
            <a href="/" class="inline-block bg-gray-200 text-gray-700 px-6 py-2 rounded-lg hover:bg-gray-300 transition">
                <i class="fas fa-arrow-left"></i> Back to Home
            </a>
        </div>
    </div>
    
    <script>
        function trackClick(url) {
            fetch('/click', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url })
            });
        }
        
        function rateUrl(url, rating) {
            fetch('/rate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url, rating })
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    location.reload();
                }
            });
        }
    </script>
</body>
</html>
'''

# Domain template
domain_template = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ domain_name }} - Web Directory</title>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0-beta3/css/all.min.css">
    <style>
        .star-rating {
            color: #fbbf24;
        }
        .url-item {
            transition: all 0.2s;
            cursor: pointer;
        }
        .url-item:hover {
            transform: translateY(-2px);
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
        }
    </style>
</head>
<body class="bg-gray-50">
    <div class="container mx-auto px-4 py-8">
        <!-- Header -->
        <header class="text-center mb-12">
            <h1 class="text-3xl font-bold text-blue-800 mb-2">{{ domain_name }}</h1>
            <p class="text-gray-600">{{ "{:,}".format(domain_count) }} websites from this domain</p>
        </header>
        
        <!-- Websites from this domain -->
        <div class="space-y-4">
            {% for url in urls %}
            <div class="url-item bg-white rounded-lg p-4 shadow-sm hover:shadow-md transition" onclick="window.open('{{ url.url }}', '_blank'); trackClick('{{ url.url }}')">
                <div class="flex items-start">
                    <div class="flex-1">
                        <h3 class="font-semibold text-lg mb-1">
                            {{ url.title|shorten(50) }}
                        </h3>
                        {% if url.category %}
                        <div class="mb-2">
                            <span class="text-xs px-2 py-1 rounded bg-blue-100 text-blue-800">{{ url.category }}</span>
                        </div>
                        {% endif %}
                    </div>
                </div>
                <p class="text-gray-600 text-sm mb-3">{{ url.description|shorten(100) }}</p>
                <div class="flex justify-between items-center">
                    <div class="star-rating" title="{{ url.rating }} stars" onclick="event.stopPropagation();">
                        {% for i in range(1, 6) %}
                            <span onclick="rateUrl('{{ url.url }}', {{ i }})" class="cursor-pointer">
                                {{ '★' if i <= url.rating|round else '☆' }}
                            </span>
                        {% endfor %}
                    </div>
                    <span class="text-xs text-gray-500">{{ "{:,}".format(url.clicks) }} visits</span>
                </div>
            </div>
            {% endfor %}
        </div>
        
        <!-- Back to Home -->
        <div class="mt-8 text-center">
            <a href="/" class="inline-block bg-gray-200 text-gray-700 px-6 py-2 rounded-lg hover:bg-gray-300 transition">
                <i class="fas fa-arrow-left"></i> Back to Home
            </a>
        </div>
    </div>
    
    <script>
        function trackClick(url) {
            fetch('/click', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url })
            });
        }
        
        function rateUrl(url, rating) {
            fetch('/rate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url, rating })
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    location.reload();
                }
            });
        }
    </script>
</body>
</html>
'''

# Write template files
with open(os.path.join(template_dir, 'index.html'), 'w') as f:
    f.write(index_template)

with open(os.path.join(template_dir, 'search.html'), 'w') as f:
    f.write(search_template)

with open(os.path.join(template_dir, 'all.html'), 'w') as f:
    f.write(all_template)

with open(os.path.join(template_dir, 'category.html'), 'w') as f:
    f.write(category_template)

with open(os.path.join(template_dir, 'domain.html'), 'w') as f:
    f.write(domain_template)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, threaded=True)