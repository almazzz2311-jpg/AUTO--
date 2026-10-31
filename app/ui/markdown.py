"""Local Markdown + mathtext rendering. No remote JS, HTML, or remote images."""
import io
import re
from functools import lru_cache

from markdown_it import MarkdownIt


@lru_cache(maxsize=128)
def math_png(expression, color):
    from matplotlib.mathtext import math_to_image
    output = io.BytesIO()
    # mathtext deliberately excludes full TeX, shell escape, and external resources.
    expression = expression.replace(r'\dfrac', r'\frac')
    from matplotlib import rc_context
    with rc_context({'savefig.transparent': True}):
        math_to_image('$' + expression.strip() + '$', output, dpi=150, format='png', color=color)
    return output.getvalue()


def safe_markdown(text):
    md = MarkdownIt('commonmark', {'html': False})
    md.disable('image')
    return md.render(text)


def render(text, color='#a99aff'):
    images = {}
    def replace(match):
        expression = next(value for value in match.groups() if value is not None)
        if len(expression) > 2000:
            return match.group(0)
        token = f'MATHPLACEHOLDER{len(images)}TOKEN'
        try:
            images[token] = math_png(expression, color)
            return token
        except (ValueError, RuntimeError):
            return match.group(0)
    # Preserve code spans/blocks; equations inside code are literal text.
    parts = re.split(r'(```[\s\S]*?```|`[^`\n]+`)', text)
    for i in range(0, len(parts), 2):
        parts[i] = re.sub(r'\\\[([\s\S]*?)\\\]|\\\(([\s\S]*?)\\\)|\$\$([\s\S]*?)\$\$|(?<!\$)\$([^\n$]+)\$(?!\$)', replace, parts[i])
    # Untrusted model content must not fetch remote images or local files.
    html = safe_markdown(''.join(parts))
    resources = {}
    for index, (token, data) in enumerate(images.items()):
        name = f'math:formula-{index}'
        html = html.replace(token, f'<img src="{name}" />')
        resources[name] = data
    return html, resources
