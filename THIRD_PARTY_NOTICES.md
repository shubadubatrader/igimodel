# Сторонние компоненты

Программа работает локально и не передаёт чертежи или распознанные данные по сети.

- GNU LibreDWG 0.14 (`vendor/libredwg`) — чтение и преобразование DWG, лицензия GNU GPL v3 или позднее. Исходный код и условия: https://www.gnu.org/software/libredwg/ и https://github.com/LibreDWG/libredwg/releases/tag/0.14
- ezdxf — чтение DXF, лицензия MIT: https://github.com/mozman/ezdxf
- openpyxl — создание XLSX, лицензия MIT: https://foss.heptapod.net/openpyxl/openpyxl
- PyMuPDF — чтение PDF, лицензия AGPL/commercial: https://pymupdf.readthedocs.io/en/latest/about.html#license
- NumPy — обработка растровой сетки таблиц при локальном OCR, лицензия BSD-3-Clause: https://numpy.org/doc/stable/license.html
- Tesseract OCR 5 и языковые модели tessdata_fast (`vendor/tesseract`) — локальное распознавание сканированных PDF, лицензия Apache 2.0: https://github.com/tesseract-ocr/tesseract и https://github.com/tesseract-ocr/tessdata_fast

При распространении сборки следует соблюдать условия лицензий перечисленных компонентов. Для закрытого коммерческого распространения особенно проверьте условия PyMuPDF и при необходимости замените PDF-модуль на совместимую альтернативу.
