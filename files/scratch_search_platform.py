with open('src/content_platform/server/static/dashboard.js', 'r', encoding='utf-8') as f:
    for idx, line in enumerate(f, 1):
        if 'platform' in line.lower():
            print(f"{idx}: {line.strip()}")
