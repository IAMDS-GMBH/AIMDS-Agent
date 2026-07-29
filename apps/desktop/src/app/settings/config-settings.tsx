import { useStore } from '@nanostores/react'
import type { ChangeEvent, ReactNode } from 'react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import { Button } from '@/components/ui/button'
import { Command, CommandInput, CommandItem, CommandList, CommandSeparator } from '@/components/ui/command'
import { Input } from '@/components/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import {
  getElevenLabsVoices,
  getHermesConfigDefaults,
  getHermesConfigRecord,
  getHermesConfigSchema,
  saveHermesConfig
} from '@/hermes'
import { useI18n } from '@/i18n'
import { triggerHaptic } from '@/lib/haptics'
import { Check, ChevronDown } from '@/lib/icons'
import { cn } from '@/lib/utils'
import { notify, notifyError } from '@/store/notifications'
import { $toolViewMode, setToolViewMode } from '@/store/tool-view'
import { $tipMode, setTipMode } from '@/store/tip-mode'
import { persistString, storedString } from '@/lib/storage'
import { FILE_PICKER_ROOT_STORAGE_KEY } from '@/store/session'
import type { ConfigFieldSchema, HermesConfigRecord } from '@/types/hermes'

import { CONTROL_TEXT, EMPTY_SELECT_VALUE, FIELD_DESCRIPTIONS, FIELD_LABELS, SECTIONS } from './constants'
import { fieldCopyForSchemaKey } from './field-copy'
import { enumOptionsFor, getNested, prettyName, setNested } from './helpers'
import { ModelSettings } from './model-settings'
import { EmptyState, ListRow, LoadingState, SettingsContent } from './primitives'

const FALLBACK_TIMEZONES = [
  'UTC',
  'Europe/Berlin',
  'Europe/Paris',
  'Europe/Amsterdam',
  'Europe/Madrid',
  'Europe/Rome',
  'Europe/Vienna',
  'Europe/Zurich',
  'Europe/Prague',
  'Europe/Warsaw',
  'Europe/Bucharest',
  'Europe/Helsinki',
  'Europe/London',
  'America/New_York',
  'America/Los_Angeles',
  'Asia/Tokyo',
  'Asia/Singapore',
  'Australia/Sydney'
] as const

const PREFERRED_TIMEZONES = [
  'UTC',
  'Europe/Berlin',
  'Europe/Paris',
  'Europe/Amsterdam',
  'Europe/Madrid',
  'Europe/Rome',
  'Europe/Vienna',
  'Europe/Zurich',
  'Europe/Prague',
  'Europe/Warsaw',
  'Europe/London',
  'America/New_York',
  'Asia/Tokyo'
] as const

function detectSystemTimezone(): string {
  try {
    const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone?.trim()
    if (timezone) {
      return timezone
    }
  } catch {
    // Fall back to UTC.
  }
  return 'UTC'
}

function getSupportedTimezones(): string[] {
  const intl = Intl as unknown as {
    supportedValuesOf?: (key: string) => string[]
  }

  const raw = intl.supportedValuesOf?.('timeZone')
  if (!raw || raw.length === 0) {
    return [...FALLBACK_TIMEZONES]
  }

  return [...new Set(raw)].sort((a, b) => a.localeCompare(b))
}

function TimezoneField({
  value,
  onChange,
  placeholder
}: {
  value: string
  onChange: (value: string) => void
  placeholder: string
}) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const systemTimezone = useMemo(() => detectSystemTimezone(), [])

  const { preferred, all } = useMemo(() => {
    const allTimezones = getSupportedTimezones()
    const timezonesSet = new Set(allTimezones)
    const normalizedCurrent = value.trim()

    if (normalizedCurrent && !timezonesSet.has(normalizedCurrent)) {
      allTimezones.unshift(normalizedCurrent)
      timezonesSet.add(normalizedCurrent)
    }

    if (!timezonesSet.has(systemTimezone)) {
      allTimezones.unshift(systemTimezone)
      timezonesSet.add(systemTimezone)
    }

    const preferredTimezones = [systemTimezone, ...PREFERRED_TIMEZONES].filter(
      (tz, index, list) => timezonesSet.has(tz) && list.indexOf(tz) === index
    )

    const preferredSet = new Set(preferredTimezones)
    const orderedAll = allTimezones.filter(tz => !preferredSet.has(tz))

    return {
      preferred: preferredTimezones,
      all: orderedAll
    }
  }, [systemTimezone, value])

  const query = search.trim().toLowerCase()
  const filterByQuery = (tz: string) => !query || tz.toLowerCase().includes(query)
  const filteredPreferred = preferred.filter(filterByQuery)
  const filteredAll = all.filter(filterByQuery)

  const selected = value.trim()
  const selectedLabel = selected || `System (${systemTimezone})`

  return (
    <Popover onOpenChange={setOpen} open={open}>
      <PopoverTrigger asChild>
        <Button
          className={cn(CONTROL_TEXT, 'w-full justify-between font-normal')}
          type="button"
          variant="outline"
        >
          <span className="truncate text-left">{selectedLabel || placeholder}</span>
          <ChevronDown className="ml-2 size-3.5 shrink-0 opacity-70" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-[var(--radix-popover-trigger-width)] p-0">
        <Command className="bg-transparent" shouldFilter={false}>
          <CommandInput
            autoFocus
            onValueChange={setSearch}
            placeholder="Search timezone..."
            value={search}
          />
          <CommandList className="max-h-80 p-1">
            <CommandItem
              onSelect={() => {
                onChange('')
                setOpen(false)
              }}
              value="__system__"
            >
              <Check className={cn('size-3.5 shrink-0 text-primary', selected ? 'invisible' : '')} />
              <span className="min-w-0 flex-1 truncate">System ({systemTimezone})</span>
            </CommandItem>

            {filteredPreferred.length > 0 && <CommandSeparator />}

            {filteredPreferred.map(timezone => {
              const isSelected = selected === timezone
              return (
                <CommandItem
                  key={`preferred-${timezone}`}
                  onSelect={() => {
                    onChange(timezone)
                    setOpen(false)
                  }}
                  value={timezone}
                >
                  <Check className={cn('size-3.5 shrink-0 text-primary', !isSelected && 'invisible')} />
                  <span className="min-w-0 flex-1 truncate">{timezone}</span>
                </CommandItem>
              )
            })}

            {filteredAll.length > 0 && filteredPreferred.length > 0 && <CommandSeparator />}

            {filteredAll.map(timezone => {
              const isSelected = selected === timezone
              return (
                <CommandItem
                  key={timezone}
                  onSelect={() => {
                    onChange(timezone)
                    setOpen(false)
                  }}
                  value={timezone}
                >
                  <Check className={cn('size-3.5 shrink-0 text-primary', !isSelected && 'invisible')} />
                  <span className="min-w-0 flex-1 truncate">{timezone}</span>
                </CommandItem>
              )
            })}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}

function ConfigField({
  schemaKey,
  schema,
  value,
  enumOptions,
  optionLabels,
  onChange
}: {
  schemaKey: string
  schema: ConfigFieldSchema
  value: unknown
  enumOptions?: string[]
  optionLabels?: Record<string, string>
  onChange: (value: unknown) => void
}) {
  const { t } = useI18n()
  const c = t.settings.config

  const label =
    fieldCopyForSchemaKey(t.settings.fieldLabels, schemaKey) ??
    fieldCopyForSchemaKey(FIELD_LABELS, schemaKey) ??
    prettyName(schemaKey.split('.').pop() ?? schemaKey)

  const normalize = (v: string) => v.toLowerCase().replace(/[^a-z0-9]+/g, '')

  const rawDescription = (
    fieldCopyForSchemaKey(t.settings.fieldDescriptions, schemaKey) ??
    fieldCopyForSchemaKey(FIELD_DESCRIPTIONS, schemaKey) ??
    schema.description ??
    ''
  ).trim()

  const normalizedDesc = normalize(rawDescription)

  const description =
    rawDescription && normalizedDesc !== normalize(label) && normalizedDesc !== normalize(schemaKey)
      ? rawDescription
      : undefined

  const row = (action: ReactNode, wide = false) => (
    <ListRow action={action} description={description} title={label} wide={wide} />
  )

  if (schema.type === 'boolean') {
    return row(
      <div className="flex items-center justify-end">
        <Switch checked={Boolean(value)} onCheckedChange={onChange} />
      </div>
    )
  }

  if (schemaKey === 'timezone') {
    return row(
      <TimezoneField
        onChange={next => onChange(next)}
        placeholder={c.notSet}
        value={String(value ?? '')}
      />
    )
  }

  const selectOptions = enumOptions ?? (schema.type === 'select' ? (schema.options ?? []).map(String) : undefined)

  if (selectOptions) {
    return row(
      <Select
        onValueChange={next => onChange(next === EMPTY_SELECT_VALUE ? '' : next)}
        value={String(value ?? '') || EMPTY_SELECT_VALUE}
      >
        <SelectTrigger className={CONTROL_TEXT}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {selectOptions.map(option => (
            <SelectItem key={option || EMPTY_SELECT_VALUE} value={option || EMPTY_SELECT_VALUE}>
              {option
                ? (optionLabels?.[option] ?? prettyName(option))
                : schemaKey === 'display.personality'
                  ? c.none
                  : c.noneParen}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    )
  }

  if (schema.type === 'number') {
    return row(
      <Input
        className={CONTROL_TEXT}
        onChange={e => {
          const raw = e.target.value
          const n = raw === '' ? 0 : Number(raw)

          if (!Number.isNaN(n)) {
            onChange(n)
          }
        }}
        placeholder={c.notSet}
        type="number"
        value={value === undefined || value === null ? '' : String(value)}
      />
    )
  }

  if (schema.type === 'list') {
    return row(
      <Input
        className={CONTROL_TEXT}
        onChange={e =>
          onChange(
            e.target.value
              .split(',')
              .map(s => s.trim())
              .filter(Boolean)
          )
        }
        placeholder={c.commaSeparated}
        value={Array.isArray(value) ? value.join(', ') : String(value ?? '')}
      />
    )
  }

  if (typeof value === 'object' && value !== null) {
    return row(
      <Textarea
        className={cn('min-h-28 resize-y bg-background font-mono', CONTROL_TEXT)}
        onChange={e => {
          try {
            onChange(JSON.parse(e.target.value))
          } catch {
            /* keep last valid */
          }
        }}
        placeholder={c.notSet}
        spellCheck={false}
        value={JSON.stringify(value, null, 2)}
      />,
      true
    )
  }

  const isLong = schema.type === 'text' || String(value ?? '').length > 100

  return row(
    isLong ? (
      <Textarea
        className={cn('min-h-24 resize-y bg-background', CONTROL_TEXT)}
        onChange={e => onChange(e.target.value)}
        placeholder={c.notSet}
        value={String(value ?? '')}
      />
    ) : (
      <Input
        className={CONTROL_TEXT}
        onChange={e => onChange(e.target.value)}
        placeholder={c.notSet}
        value={String(value ?? '')}
      />
    ),
    isLong
  )
}

export function ConfigSettings({
  activeSectionId,
  onConfigSaved,
  onMainModelChanged,
  importInputRef
}: {
  activeSectionId: string
  onConfigSaved?: () => void
  onMainModelChanged?: (provider: string, model: string) => void
  importInputRef: React.RefObject<HTMLInputElement | null>
}) {
  const { t } = useI18n()
  const c = t.settings.config
  const a = t.settings.appearance
  const toolViewMode = useStore($toolViewMode)
  const tipMode = useStore($tipMode)
  const [config, setConfig] = useState<HermesConfigRecord | null>(null)
  const [_defaults, setDefaults] = useState<HermesConfigRecord | null>(null)
  const [schema, setSchema] = useState<Record<string, ConfigFieldSchema> | null>(null)
  const [elevenLabsVoiceOptions, setElevenLabsVoiceOptions] = useState<string[] | null>(null)
  const [elevenLabsVoiceLabels, setElevenLabsVoiceLabels] = useState<Record<string, string>>({})
  const [updateChannel, setUpdateChannel] = useState<'stable' | 'main'>('main')
  const [filePickerRoot, setFilePickerRoot] = useState<'userDir' | 'vault'>(() => {
    const saved = storedString(FILE_PICKER_ROOT_STORAGE_KEY)
    return saved === 'vault' ? 'vault' : 'userDir'
  })
  const saveVersionRef = useRef(0)
  const [saveVersion, setSaveVersion] = useState(0)

  useEffect(() => {
    if (window.hermesDesktop?.updates?.getBranch) {
      window.hermesDesktop.updates
        .getBranch()
        .then(res => {
          if (res?.branch) {
            setUpdateChannel(res.branch === 'tags' || res.branch === 'stable' ? 'stable' : 'main')
          }
        })
        .catch(() => {})
    }
  }, [])

  const updateChannelOptions = [
    { id: 'stable', label: a.updateChannelStable },
    { id: 'main', label: a.updateChannelMain }
  ] as const

  const filePickerRootOptions = [
    { id: 'userDir', label: a.filePickerRootUserDir },
    { id: 'vault', label: a.filePickerRootVault }
  ] as const

  const toolOptions = [
    { id: 'product', label: a.product },
    { id: 'technical', label: a.technical }
  ] as const

  const tipOptions = [
    { id: 'auto', label: a.tipModeAuto },
    { id: 'business', label: a.tipModeBusiness },
    { id: 'nerd', label: a.tipModeNerd }
  ] as const

  useEffect(() => {
    let cancelled = false
    Promise.all([getHermesConfigRecord(), getHermesConfigDefaults(), getHermesConfigSchema()])
      .then(([c, d, s]) => {
        if (cancelled) {
          return
        }

        setConfig(c)
        setDefaults(d)
        setSchema(s.fields)
      })
      .catch(err => notifyError(err, c.failedLoad))

    return () => void (cancelled = true)
  }, [])

  useEffect(() => {
    let cancelled = false

    getElevenLabsVoices()
      .then(result => {
        if (cancelled || !result.available) {
          return
        }

        setElevenLabsVoiceOptions(result.voices.map(voice => voice.voice_id))
        setElevenLabsVoiceLabels(Object.fromEntries(result.voices.map(voice => [voice.voice_id, voice.label])))
      })
      .catch(() => {
        if (!cancelled) {
          setElevenLabsVoiceOptions(null)
          setElevenLabsVoiceLabels({})
        }
      })

    return () => void (cancelled = true)
  }, [])

  useEffect(() => {
    if (!config || saveVersion === 0) {
      return
    }

    const v = saveVersion

    const t = window.setTimeout(() => {
      void (async () => {
        try {
          await saveHermesConfig(config)

          if (saveVersionRef.current === v) {
            onConfigSaved?.()
          }
        } catch (err) {
          if (saveVersionRef.current === v) {
            notifyError(err, c.autosaveFailed)
          }
        }
      })()
    }, 550)

    return () => window.clearTimeout(t)
  }, [config, onConfigSaved, saveVersion])

  const updateConfig = (next: HermesConfigRecord) => {
    saveVersionRef.current += 1
    setConfig(next)
    setSaveVersion(saveVersionRef.current)
  }

  const sectionFields = useMemo(() => {
    if (!schema) {
      return new Map<string, [string, ConfigFieldSchema][]>()
    }

    return new Map(
      SECTIONS.map(s => [s.id, s.keys.flatMap(k => (schema[k] ? [[k, schema[k]] as [string, ConfigFieldSchema]] : []))])
    )
  }, [schema])

  const fields = sectionFields.get(activeSectionId) ?? []

  // Deep-link target from the command palette (?field=<key>): scroll the row
  // into view and flash it, then drop the param so it doesn't re-fire.
  const [searchParams, setSearchParams] = useSearchParams()
  const targetField = searchParams.get('field')

  useEffect(() => {
    if (!targetField || !config || !schema) {
      return
    }

    const element = document.getElementById(`setting-field-${targetField}`)

    if (!element) {
      return
    }

    element.scrollIntoView({ behavior: 'smooth', block: 'center' })
    element.classList.add('setting-field-highlight')

    const timeout = window.setTimeout(() => element.classList.remove('setting-field-highlight'), 1600)

    setSearchParams(
      previous => {
        const next = new URLSearchParams(previous)
        next.delete('field')

        return next
      },
      { replace: true }
    )

    return () => window.clearTimeout(timeout)
  }, [config, schema, setSearchParams, targetField])

  function handleImport(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]

    if (!file) {
      return
    }

    const reader = new FileReader()

    reader.onload = () => {
      try {
        updateConfig(JSON.parse(String(reader.result)))
        notify({ kind: 'success', title: c.imported, message: t.common.saving })
      } catch (err) {
        notifyError(err, c.invalidJson)
      }
    }

    reader.readAsText(file)
    e.target.value = ''
  }

  if (!config || !schema) {
    return <LoadingState label={c.loading} />
  }

  return (
    <SettingsContent>
      {activeSectionId === 'model' && (
        <div className="mb-6">
          <ModelSettings onMainModelChanged={onMainModelChanged} />
        </div>
      )}
      {activeSectionId === 'advanced' && (
        <div className="mb-6 space-y-6">
          <div>
            <h3 className="mb-2 px-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              {a.sectionUpdateOptions}
            </h3>
            <div className="divide-y divide-(--ui-stroke-tertiary) rounded-lg border border-(--ui-stroke-tertiary) bg-(--ui-bg-quinary) px-3 py-1">
              <ListRow
                action={
                  <SegmentedControl
                    onChange={id => {
                      triggerHaptic('selection')
                      const branchName = id === 'stable' ? 'tags' : 'main'
                      setUpdateChannel(id as 'stable' | 'main')
                      window.hermesDesktop?.updates?.setBranch?.(branchName).catch(() => {})
                      notify({ kind: 'info', title: c.restartNoticeTitle, message: c.restartNoticeDesc })
                    }}
                    options={updateChannelOptions}
                    value={updateChannel}
                  />
                }
                description={a.updateChannelDesc}
                title={a.updateChannelTitle}
              />
              <ListRow
                action={
                  <div className="flex items-center gap-2">
                    <SegmentedControl
                      onChange={id => {
                        triggerHaptic('selection')
                        setFilePickerRoot(id as 'userDir' | 'vault')
                        persistString(FILE_PICKER_ROOT_STORAGE_KEY, id)
                        notify({ kind: 'info', title: c.restartNoticeTitle, message: c.restartNoticeDesc })
                      }}
                      options={filePickerRootOptions}
                      value={filePickerRoot}
                    />
                    {Boolean(window.hermesDesktop?.relaunchApp) && (
                      <Button
                        onClick={() => {
                          void window.hermesDesktop?.relaunchApp?.()
                        }}
                        size="sm"
                        variant="outline"
                      >
                        {a.relaunchClient}
                      </Button>
                    )}
                  </div>
                }
                description={a.filePickerRootDesc}
                title={a.filePickerRootTitle}
              />
            </div>
          </div>

          <div>
            <h3 className="mb-2 px-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              {a.sectionCustomerExperience}
            </h3>
            <div className="divide-y divide-(--ui-stroke-tertiary) rounded-lg border border-(--ui-stroke-tertiary) bg-(--ui-bg-quinary) px-3 py-1">
              <ListRow
                action={
                  <SegmentedControl
                    onChange={id => {
                      triggerHaptic('selection')
                      setToolViewMode(id)
                    }}
                    options={toolOptions}
                    value={toolViewMode}
                  />
                }
                description={a.toolViewDesc}
                title={a.toolViewTitle}
              />
              <ListRow
                action={
                  <SegmentedControl
                    onChange={id => {
                      triggerHaptic('selection')
                      setTipMode(id)
                    }}
                    options={tipOptions}
                    value={tipMode}
                  />
                }
                description={a.tipModeDesc}
                title={a.tipModeTitle}
              />
            </div>
          </div>

          {fields.length > 0 && (
            <div>
              <h3 className="mb-2 px-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                {a.sectionAdvancedConfig}
              </h3>
            </div>
          )}
        </div>
      )}
      {fields.length === 0 ? (
        <EmptyState description={c.emptyDesc} title={c.emptyTitle} />
      ) : (
        <div className="grid gap-1">
          {fields.map(([key, field]) => (
            <div className="scroll-mt-6 rounded-lg" id={`setting-field-${key}`} key={key}>
              <ConfigField
                enumOptions={
                  key === 'tts.elevenlabs.voice_id'
                    ? enumOptionsFor(key, getNested(config, key), config, elevenLabsVoiceOptions ?? undefined)
                    : enumOptionsFor(key, getNested(config, key), config)
                }
                onChange={value => updateConfig(setNested(config, key, value))}
                optionLabels={key === 'tts.elevenlabs.voice_id' ? elevenLabsVoiceLabels : undefined}
                schema={field}
                schemaKey={key}
                value={getNested(config, key)}
              />
            </div>
          ))}
        </div>
      )}
      <input
        accept=".json,application/json"
        className="hidden"
        onChange={handleImport}
        ref={importInputRef}
        type="file"
      />
    </SettingsContent>
  )
}
