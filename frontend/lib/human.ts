import type { FixOutcome, Issue } from "./types";

/** Человеческие описания проблем: код остаётся только в подсказке при наведении. */
export const ISSUE_TEXT: Record<string, string> = {
  text_overflow: "Текст не поместился на слайде",
  text_on_decor_edge: "Часть текста не видна на фоне",
  text_invisible: "Текст сливается с фоном и не читается",
  empty_text_frame: "Текст потерялся: пустая рамка на слайде",
  overlap: "Два блока наложились друг на друга",
  out_of_bounds: "Элемент выходит за границы слайда",
  font_not_allowed: "Шрифт не из шаблона",
  font_size_not_in_scale: "Размер шрифта не из шаблона",
  color_not_allowed: "Цвет не из палитры шаблона",
  contrast_too_low: "Текст плохо читается на фоне",
  too_many_bullets: "На слайде слишком много пунктов",
  bullet_too_long: "Пункт слишком длинный",
  slide_too_dense: "Слайд перегружен содержимым",
  slide_too_sparse: "На слайде мало содержимого",
  empty_slide: "Слайд без содержимого",
  duplicate_slide: "Два слайда повторяют друг друга",
  duplicate_heading: "Заголовки повторяются",
  placeholder_text: "Остался текст-заглушка",
  table_too_big: "Таблица слишком большая",
  chart_unlabeled: "Диаграмма непонятна без подписей",
  too_many_series: "Слишком много линий на диаграмме",
  image_missing: "Не хватает картинки",
  image_stretched: "Картинка растянута",
  raster_slide: "Слайд — картинка, а не редактируемые объекты",
  content_in_margins: "Текст заходит на поля у края слайда",
  misaligned_to_grid: "Блоки не выровнены между собой",
  layout_not_from_template: "Макет не из шаблона",
  branding_shifted: "Логотип или подпись сдвинулись",
  too_many_typefaces: "Слишком много разных шрифтов",
  content_off_source: "Слайд слабо опирается на источник",
  fact_unverified: "Число не найдено в источнике",
};

/** Проблемы, которые движок правит сам (см. backend audit/fixes.py). */
export const FIXABLE_CODES = new Set([
  "font_size_not_in_scale", "text_overflow", "contrast_too_low",
  "bullet_too_long", "too_many_bullets", "slide_too_dense", "slide_too_sparse",
  "empty_slide", "image_missing", "table_too_big", "chart_unlabeled",
  "too_many_series", "placeholder_text", "misaligned_to_grid",
  "content_in_margins", "layout_not_from_template", "branding_shifted",
  "duplicate_slide", "duplicate_heading", "image_stretched",
]);

/** Смысловые (недетерминированные) замечания: VLM и опора на источник. */
export const SEMANTIC_PREFIXES = ["vlm_", "content_off_source", "fact_unverified", "off_source"];

export const ACTION_TEXT: Record<string, string> = {
  shrink_font: "уменьшен кегль",
  split_bullet: "длинные пункты разделены",
  split_slide: "перегруженный слайд разбит на два",
  merge_slide: "пустые слайды объединены",
  table_to_chart: "таблица заменена диаграммой",
  chart_to_table: "диаграмма заменена таблицей",
  trim_table: "таблица обрезана до допустимого размера",
  drop_block: "убран блок-заглушка",
  drop_image: "убрана картинка без файла",
  switch_layout: "подобран другой макет",
  drop_duplicate: "удалён слайд-дубликат",
  rename_heading: "уточнены повторяющиеся заголовки",
  limit_series: "оставлены пять линий на диаграмме",
};

export function isSemantic(code: string): boolean {
  return SEMANTIC_PREFIXES.some((prefix) => code.startsWith(prefix));
}

export function isFixable(code: string): boolean {
  return FIXABLE_CODES.has(code);
}

/** Описание проблемы простыми словами; код — в title (подсказка при наведении). */
export function issueText(issue: Issue): string {
  const code = issue.code;
  if (code.startsWith("vlm_")) {
    const message = issue.message.replace(/^VLM:\s*/, "");
    const [criterion, detail] = message.split(" — ");
    return detail || criterion;
  }
  if (code.startsWith("content_off_source")) {
    return "Слайд слабо опирается на загруженные факты";
  }
  if (code.startsWith("fact_unverified")) {
    return issue.message.replace(/^[^:]*:\s*/, "") || "Число не найдено в источнике";
  }
  return ISSUE_TEXT[code] || "Замечание проверки";
}

export function issueWhere(issue: Issue): string {
  return issue.slide >= 0 ? `Слайд ${issue.slide + 1}` : "Вся презентация";
}

export function fixText(outcome: FixOutcome): string {
  const action = ACTION_TEXT[outcome.action] || outcome.action || "исправлено";
  const where = outcome.slide >= 0 ? `слайд ${outcome.slide + 1}: ` : "";
  return `${where}${action}`;
}
