import type { FixOutcome, Issue } from "./types";

/**
 * Человеческие ярлыки аудита: коды проверок остаются только в подсказке
 * (title) и в API, а на экране — понятные названия и объяснения.
 */

/** Сколько правил вёрстки проверяет детерминированный слой (см. backend/audit). */
export const DETERMINISTIC_RULES = 29;
/** Сколько смысловых вопросов задаёт нейросеть (Приложение 1 ТЗ). */
export const SEMANTIC_QUESTIONS = 11;

export type AuditLabel = {
  title: string;
  explanation: string;
  /** группа нужна для иконок и сортировки в блоке качества */
  group: "layout" | "text" | "tokens" | "data" | "source" | "structure";
};

export const AUDIT_LABELS: Record<string, AuditLabel> = {
  text_overflow: {
    title: "Текст не поместился на слайде",
    explanation: "Строка вышла за рамку блока и может обрезаться при показе. "
      + "Обычно лечится сокращением текста или уменьшением кегля.",
    group: "text",
  },
  text_on_decor_edge: {
    title: "Часть текста не видна на фоне",
    explanation: "Текст заходит на цветную панель шаблона, и часть букв "
      + "теряется. Помогает перенос блока на однотонный участок.",
    group: "text",
  },
  text_invisible: {
    title: "Текст сливается с фоном",
    explanation: "Цвет текста почти совпадает с фоном — надпись не читается.",
    group: "text",
  },
  empty_text_frame: {
    title: "Пустая текстовая рамка",
    explanation: "На слайде остался блок без текста — вероятно, содержимое "
      + "потерялось при вёрстке.",
    group: "text",
  },
  overlap: {
    title: "Два блока наложились",
    explanation: "Элементы перекрывают друг друга: часть одного закрыта другим.",
    group: "layout",
  },
  out_of_bounds: {
    title: "Элемент выходит за границы слайда",
    explanation: "Часть блока уходит за край — при показе она не видна.",
    group: "layout",
  },
  content_in_margins: {
    title: "Текст заходит на поля",
    explanation: "Блок стоит слишком близко к краю слайда, ближе, чем "
      + "предусматривает шаблон.",
    group: "layout",
  },
  misaligned_to_grid: {
    title: "Блоки не выровнены",
    explanation: "Края соседних блоков немного расходятся — заметно как "
      + "случайный сдвиг.",
    group: "layout",
  },
  font_not_allowed: {
    title: "Шрифт не из шаблона",
    explanation: "Для текста использован шрифт, которого нет в дизайн-системе "
      + "шаблона.",
    group: "tokens",
  },
  font_size_not_in_scale: {
    title: "Размер шрифта не из шаблона",
    explanation: "Кегль не входит в типографическую шкалу шаблона.",
    group: "tokens",
  },
  color_not_allowed: {
    title: "Цвет не из палитры шаблона",
    explanation: "Использован оттенок, которого нет в фирменной палитре.",
    group: "tokens",
  },
  contrast_too_low: {
    title: "Низкий контраст текста",
    explanation: "Текст читается хуже нормы WCAG — на ярком экране или при "
      + "печати может пропасть.",
    group: "tokens",
  },
  too_many_typefaces: {
    title: "Слишком много шрифтов",
    explanation: "В колоде больше гарнитур, чем допускает шаблон: обычно не "
      + "больше двух.",
    group: "tokens",
  },
  too_many_bullets: {
    title: "Слишком много пунктов",
    explanation: "На слайде больше шести пунктов — список тяжело читать.",
    group: "structure",
  },
  bullet_too_long: {
    title: "Пункт слишком длинный",
    explanation: "В одном пункте больше 15 слов — его лучше разделить.",
    group: "structure",
  },
  slide_too_dense: {
    title: "Слайд перегружен контентом",
    explanation: "Блоки занимают больше места, чем предусматривает макет.",
    group: "structure",
  },
  slide_too_sparse: {
    title: "На слайде мало содержимого",
    explanation: "Контент занимает малую часть слайда — выглядит пустым.",
    group: "structure",
  },
  empty_slide: {
    title: "Пустой слайд",
    explanation: "На слайде нет ни текста, ни объектов.",
    group: "structure",
  },
  duplicate_slide: {
    title: "Дубль слайда",
    explanation: "Два слайда повторяют друг друга.",
    group: "structure",
  },
  duplicate_heading: {
    title: "Повторяющиеся заголовки",
    explanation: "Один и тот же заголовок встречается на нескольких слайдах.",
    group: "structure",
  },
  placeholder_text: {
    title: "Остался текст-заглушка",
    explanation: "В тексте сохранилась служебная «рыба» вместо содержания.",
    group: "structure",
  },
  table_too_big: {
    title: "Таблица слишком большая",
    explanation: "Строк или колонок больше, чем помещается на слайд читаемо "
      + "(максимум 7×5).",
    group: "data",
  },
  chart_unlabeled: {
    title: "Диаграмма без подписей",
    explanation: "У диаграммы нет легенды или единиц измерения — значения "
      + "непонятны без пояснений.",
    group: "data",
  },
  too_many_series: {
    title: "Слишком много линий на диаграмме",
    explanation: "Серий больше пяти — график становится нечитаемым.",
    group: "data",
  },
  image_missing: {
    title: "Не хватает картинки",
    explanation: "Для блока-иллюстрации не нашлось изображения: остался "
      + "пустой слот шаблона.",
    group: "data",
  },
  image_stretched: {
    title: "Картинка растянута",
    explanation: "Пропорции изображения искажены при вставке.",
    group: "data",
  },
  raster_slide: {
    title: "Слайд — готовая картинка",
    explanation: "Слайд вставлен изображением, а не редактируемыми объектами. "
      + "Такой файл не засчитывается по требованиям.",
    group: "data",
  },
  layout_not_from_template: {
    title: "Макет не из шаблона",
    explanation: "Слайд собран на макете, которого нет в загруженном файле.",
    group: "layout",
  },
  branding_shifted: {
    title: "Логотип или подпись сдвинулись",
    explanation: "Фирменный элемент сместился с места, предусмотренного "
      + "шаблоном.",
    group: "layout",
  },
  content_off_source: {
    title: "Слайд слабо опирается на источник",
    explanation: "Смысл слайда далеко от загруженных фактов и брифа — "
      + "проверьте, нет ли выдуманных утверждений.",
    group: "source",
  },
  fact_unverified: {
    title: "Число не найдено в источниках",
    explanation: "Цифра со слайда не встречается ни в брифе, ни в файле "
      + "с фактами. Убедитесь, что она верна.",
    group: "source",
  },
};

const FALLBACK: AuditLabel = {
  title: "",
  explanation: "Служебная проверка вёрстки. Если непонятно, что делать — "
    + "скачайте PPTX и посмотрите слайд.",
  group: "layout",
};

export function labelFor(code: string): AuditLabel {
  return AUDIT_LABELS[code] || {
    ...FALLBACK,
    title: `Проверка: ${code}`,
  };
}

/** Проблемы, которые движок правит сам (см. backend audit/fixes.py). */
export const FIXABLE_CODES = new Set([
  "font_size_not_in_scale", "text_overflow", "contrast_too_low",
  "bullet_too_long", "too_many_bullets", "slide_too_dense", "slide_too_sparse",
  "empty_slide", "image_missing", "table_too_big", "chart_unlabeled",
  "too_many_series", "placeholder_text", "misaligned_to_grid",
  "content_in_margins", "layout_not_from_template", "branding_shifted",
  "duplicate_slide", "duplicate_heading", "image_stretched",
]);

/** Смысловые замечания нейросети: VLM-критерии (Приложение 1 ТЗ). */
export function isSemantic(code: string): boolean {
  return code.startsWith("vlm_");
}

export function isFixable(code: string): boolean {
  return FIXABLE_CODES.has(code);
}

function parseVlm(issue: Issue): { criterion: string; detail: string } {
  const message = issue.message.replace(/^VLM:\s*/, "");
  const [criterion, detail] = message.split(" — ");
  return { criterion: criterion || "", detail: detail || criterion || "" };
}

/** Заголовок проблемы: для смысловых — нейтрально, для остальных — ярлык. */
export function issueTitle(issue: Issue): string {
  if (isSemantic(issue.code)) {
    return parseVlm(issue).criterion || "Замечание нейросети";
  }
  return labelFor(issue.code).title;
}

/** Короткая цитата модели (для смысловой секции). */
export function issueQuote(issue: Issue): string {
  if (isSemantic(issue.code)) {
    return parseVlm(issue).detail;
  }
  if (issue.code === "fact_unverified") {
    return issue.message.replace(/^[^:]*:\s*/, "");
  }
  return issue.message;
}

/** Пояснение простыми словами для «Подробнее». */
export function issueExplanation(issue: Issue): string {
  if (isSemantic(issue.code)) {
    return "Это оценка нейросети по изображению слайда. Она может меняться "
      + "от запуска к запуску — решайте сами, критично ли это.";
  }
  return labelFor(issue.code).explanation;
}

export function issueWhere(issue: Issue): string {
  return issue.slide >= 0 ? `Слайд ${issue.slide + 1}` : "Вся презентация";
}

const FIX_ACTION_TEXT: Record<string, string> = {
  shrink_font: "Уменьшен кегль",
  split_bullet: "Длинные пункты разделены",
  split_slide: "Перегруженный слайд разбит",
  merge_slide: "Пустые слайды объединены",
  table_to_chart: "Таблица заменена диаграммой",
  chart_to_table: "Диаграмма заменена таблицей",
  trim_table: "Таблица обрезана до допустимого размера",
  drop_block: "Убран блок-заглушка",
  drop_image: "Убрана картинка без файла",
  switch_layout: "Подобран другой макет",
  drop_duplicate: "Удалён слайд-дубликат",
  rename_heading: "Уточнены повторяющиеся заголовки",
  limit_series: "Оставлены пять линий на диаграмме",
};

/** «Перегруженный слайд разбит (слайд 2)» — глагол + объект + слайд. */
export function fixActionText(outcome: FixOutcome): string {
  const action = FIX_ACTION_TEXT[outcome.action]
    || (outcome.action ? outcome.action.replace(/_/g, " ") : "Исправлено");
  const where = outcome.slide >= 0 ? ` (слайд ${outcome.slide + 1})` : "";
  return `${action}${where}`;
}

/** «1.2 МБ», «340 КБ» — размер файла для кнопки. */
export function formatBytes(bytes: number | null | undefined): string {
  if (!bytes || bytes <= 0) return "";
  const mb = bytes / (1024 * 1024);
  if (mb >= 0.95) return `${mb.toFixed(1).replace(".", ",")} МБ`;
  const kb = Math.max(1, Math.round(bytes / 1024));
  return `${kb} КБ`;
}

export function plural(n: number, one: string, few: string, many: string): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}
