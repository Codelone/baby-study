import { languageLabels } from './catalog'
import type { Language } from './catalog'
import { emptyFields, fieldLabels } from './intake'
import type { BookFields } from './intake'

export default function BookFieldsEditor({
  fields,
  disabled,
  onField,
}: {
  fields: Partial<BookFields>
  disabled: boolean
  onField: <K extends keyof BookFields>(key: K, value: BookFields[K]) => void
}) {
  const values = { ...emptyFields(), ...fields }
  function field(key: keyof BookFields) {
    const value = values[key]
    return (
      <label className={'book-field field-' + key} key={key}>
        <span>
          {fieldLabels[key]}
          {key === 'title' ? '（必填）' : ''}
        </span>
        {key === 'language' ? (
          <select
            value={values.language}
            disabled={disabled}
            onChange={(event) =>
              onField('language', event.target.value as Language)
            }
          >
            {Object.entries(languageLabels).map(([code, label]) => (
              <option value={code} key={code}>
                {label}
              </option>
            ))}
          </select>
        ) : key === 'pages' ? (
          <input
            type="number"
            min="1"
            step="1"
            value={values.pages ?? ''}
            disabled={disabled}
            onChange={(event) =>
              onField(
                'pages',
                event.target.value === '' ? null : Number(event.target.value),
              )
            }
          />
        ) : Array.isArray(value) ? (
          <textarea
            aria-label={fieldLabels[key]}
            rows={2}
            value={value.join('\n')}
            placeholder="每行一项，未知留空"
            disabled={disabled}
            onChange={(event) => onField(key, event.target.value.split('\n'))}
          />
        ) : key === 'description' ? (
          <textarea
            aria-label={fieldLabels[key]}
            rows={4}
            value={value || ''}
            disabled={disabled}
            onChange={(event) => onField('description', event.target.value)}
          />
        ) : (
          <input
            type={
              key === 'cover_url' || key === 'cover_source_url' ? 'url' : 'text'
            }
            value={String(value ?? '')}
            required={key === 'title'}
            disabled={disabled}
            placeholder={key === 'isbn' ? '核对具体版本，未知留空' : undefined}
            onChange={(event) => onField(key, event.target.value)}
          />
        )}
      </label>
    )
  }
  return (
    <>
      <div className="book-fields">
        {(
          [
            'title',
            'language',
            'isbn',
            'authors',
            'publisher',
            'edition',
          ] as const
        ).map(field)}
      </div>
      <details className="extra-fields">
        <summary>更多资料（绘者、译者、出版、封面等）</summary>
        <div className="book-fields">
          {(
            [
              'original_title',
              'illustrators',
              'translators',
              'publication_date',
              'binding',
              'pages',
              'themes',
              'cover_url',
              'cover_source_url',
              'description',
            ] as const
          ).map(field)}
        </div>
      </details>
    </>
  )
}
