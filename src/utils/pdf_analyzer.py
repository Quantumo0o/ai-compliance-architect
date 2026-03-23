import re
import pdfplumber

def should_ocr(page: pdfplumber.page.Page, text: str) -> bool:
    score = 0
    text = text or ""
    
    if len(text.strip()) < 200:
        score += 3
        
    vector_count = len(page.lines) + len(page.rects) + len(page.curves)
    if vector_count > 50:
        score += 2
        
    if len(text) > 0:
        whitespace_ratio = (len(text) - len(text.strip())) / len(text)
        if whitespace_ratio > 0.5:
            score += 1
            
    if len(text.strip()) > 0:
        tokens = text.split()
        if tokens:
            valid_words = [t for t in tokens if re.match(r'^[A-Za-z]{2,}$', t)]
            valid_ratio = len(valid_words) / len(tokens)
            if valid_ratio < 0.2:
                score += 3

    return score >= 3
