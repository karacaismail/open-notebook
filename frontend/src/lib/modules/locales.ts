import { moduleEntries } from './generated'

const en = {
  title: 'Modules', subtitle: 'Optional features, separate from the core notebook.',
  enabled: 'Enabled', disabled: 'Disabled', enable: 'Enable', disable: 'Disable',
  unavailable: 'This module is disabled or has not been included in this build.',
  error: 'Could not load module configuration.', retry: 'Try again',
  dependencies: 'Dependencies', noDependencies: 'None', installed: 'Installed', notInstalled: 'Not installed',
  runtime: 'Runtime module', build: 'Build customization', external: 'Local service / setup',
  runtimeHelp: 'Controls access to this module in Open Notebook. Disabling retains its data and does not uninstall its local service. Active and unpaused jobs must finish or be paused first.',
  buildHelp: 'Selected during the build. Rebuild without this module to restore the core files; notebook data is retained.',
  externalHelp: 'Installed by the operator. Manage the local service using its installation instructions; this switch does not terminate operating-system processes.',
  installHelp: 'Include this module with scripts/prepare_modules.py and follow modules/README.md.',
  saved: 'Module configuration saved.', back: 'Open notebooks', operationError: 'Module configuration could not be changed.',
}
const tr: typeof en = {
  title: 'Modüller', subtitle: 'Not defteri çekirdeğinden ayrı, isteğe bağlı özellikler.',
  enabled: 'Etkin', disabled: 'Kapalı', enable: 'Etkinleştir', disable: 'Kapat',
  unavailable: 'Bu modül kapalı veya bu derlemeye eklenmemiş.',
  error: 'Modül yapılandırması yüklenemedi.', retry: 'Tekrar dene',
  dependencies: 'Bağımlılıklar', noDependencies: 'Yok', installed: 'Kurulu', notInstalled: 'Kurulu değil',
  runtime: 'Çalışma sırasında yönetilen modül', build: 'Derleme uyarlaması', external: 'Yerel servis / kurulum',
  runtimeHelp: 'Open Notebook içinden bu modüle erişimi yönetir. Kapatmak verileri silmez, yerel servisi kaldırmaz. Önce aktif işler bitmeli veya tamamlanmamış işler duraklatılmalıdır.',
  buildHelp: 'Derleme sırasında seçilir. Çekirdek dosyalara dönmek için bu modül olmadan yeniden derleyin; not defteri verileri korunur.',
  externalHelp: 'Kurulum yöneticisi tarafından yüklenir. Yerel servisi kurulum yönergeleriyle yönetin; bu ayar işletim sistemi süreçlerini sonlandırmaz.',
  installHelp: 'Bu modülü scripts/prepare_modules.py ile derlemeye ekleyin ve modules/README.md yönergelerini izleyin.',
  saved: 'Modül yapılandırması kaydedildi.', back: 'Not defterlerini aç', operationError: 'Modül yapılandırması değiştirilemedi.',
}

export function moduleTranslations(language: string) {
  const result: Record<string, unknown> = { modules: language === 'tr-TR' ? tr : en }
  for (const entry of Object.values(moduleEntries)) {
    Object.assign(result, entry.locales?.[language] || entry.locales?.['en-US'] || {})
  }
  return result
}
