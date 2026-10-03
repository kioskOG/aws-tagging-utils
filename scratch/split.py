import os

def split_html(file_path):
    with open(file_path, 'r') as f:
        content = f.read()
        
    # Find style tag
    style_start = content.find('<style>')
    style_end = content.find('</style>')
    
    # Find script tag
    script_start = content.find('<script>')
    script_end = content.find('</script>')
    
    css_content = content[style_start + 7:style_end].strip()
    js_content = content[script_start + 8:script_end].strip()
    
    html_content = content[:style_start] + '<link rel="stylesheet" href="/static/app.css">\n' + content[style_end + 8:script_start] + '<script src="/static/app.js"></script>\n' + content[script_end + 9:]
    
    os.makedirs('web/static', exist_ok=True)
    
    with open('web/static/app.css', 'w') as f:
        f.write(css_content)
        
    with open('web/static/app.js', 'w') as f:
        f.write(js_content)
        
    with open('web/templates/index.html', 'w') as f:
        f.write(html_content)

if __name__ == '__main__':
    split_html('web/templates/index.html')
