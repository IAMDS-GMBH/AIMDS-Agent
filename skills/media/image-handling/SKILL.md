---
name: image-handling
description: Best practices for processing, analyzing, generating, cropping, compressing, and embedding images into documents, Markdown, and M365 reports. Trigger when user asks about images, diagrams, screenshots, vision tasks, or image processing.
---

# Image & Vision Handling Workflow

This skill defines standard procedures for image inspection, vision analysis, diagram generation, cropping/resizing, and incorporating images cleanly into reports, Markdown, and Office documents.

## 1. Vision & Image Analysis
- **Inspect First**: When analyzing an image, UI screenshot, or document scan, inspect the full image or relevant region before summarizing.
- **Extract Text & Diagrams**: Extract all readable text, labels, values, chart legends, and spatial layouts accurately.
- **Describe Visuals Clearly**: Structure observations into:
  - **Summary**: 1-sentence overview of the image contents.
  - **Key Elements**: Bullet points for text, UI components, data trends, or errors shown.
  - **Actionable Takeaways**: Next steps or fixes derived from the visual evidence.

## 2. Image Processing & Optimization
- **Formats & Conversion**:
  - Prefer PNG for diagrams, UI screenshots, and line art (lossless, clean text rendering).
  - Prefer JPEG/WebP for photography or dense visual backgrounds.
  - Convert SVG to PNG using `cairosvg` or `resvg` when embedding into Word/PDF documents that don't support raw SVG.
- **Resizing & Compression**:
  - Resize large screenshots (e.g. 4K displays) down to max width 1600px–1920px before embedding or sending to keep file sizes low and rendering fast.
  - Use `Pillow` (PIL) in Python or `ffmpeg`/`imagemagick` for automated cropping, resizing, and color adjustment.

## 3. Embedding Images in Documents
- **Markdown & HTML**:
  - Use relative image paths: `![Description](images/filename.png)`.
  - In Teams / M365 messages: Use inline hosted contents or OneDrive attachment cards (`m365_send_chat_message(attachments=[...])`).
- **Word Documents (.docx)**:
  - Insert images centered with captions underneath (10 pt Dunkelgrau `#7A7A80`, Italic).
  - Set image width to fit page margins (e.g., 6.0–6.5 inches max width).
- **PowerPoint & Reports**:
  - Maintain aspect ratio. Never stretch or distort images.
  - Add 1 pt thin border (`#E0E3FC`) around screenshots with white backgrounds so they don't bleed into the page.

## 4. Diagram & Architecture Generation
- When generating architecture diagrams, flowcharts, or system models:
  - Prefer Mermaid or Excalidraw / ASCII diagrams in Markdown.
  - Apply IAMDS brand colors: Primary Nodes in IAMDS Blau (`#3F59FF`), Accent Nodes in IAMDS Gold (`#FFD440`), Dark headers in Nachtblau (`#212B80`).
