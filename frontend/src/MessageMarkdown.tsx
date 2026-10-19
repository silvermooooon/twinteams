import { createContext, memo, useContext } from 'react';
import type { ComponentProps } from 'react';
import Markdown from 'react-markdown';
import type { Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { t } from './i18n';
import './MessageMarkdown.css';

const insideLink = createContext(false);
function MarkdownImage({ src, alt, title }: ComponentProps<'img'>) {
  const linked = useContext(insideLink);
  const label = alt || t('chat.messageImage');
  return !linked && src ? <a href={src} title={title} target="_blank" rel="noopener noreferrer">{label}</a> : <span>{label}</span>;
}
const components: Components = {
  a: ({ href, children, title }) => href ? <insideLink.Provider value={true}><a href={href} title={title} target="_blank" rel="noopener noreferrer">{children}</a></insideLink.Provider> : <span>{children}</span>,
  pre: ({ children }) => <pre tabIndex={0}>{children}</pre>,
  table: ({ children }) => <div className="markdown-table-scroll" role="region" aria-label={t('chat.messageTable')} tabIndex={0}><table>{children}</table></div>,
  // Message-authored image URLs are explicit links; attachments keep their authorized image flow.
  img: MarkdownImage,
};
const plugins = [remarkGfm];

/** No raw-HTML plugin: HTML stays escaped text and the default safe URL filter stays enabled. */
export const MessageMarkdown = memo(function MessageMarkdown({ text }: { text: string }) {
  return <div className="message-text markdown-content"><Markdown remarkPlugins={plugins} components={components}>{text}</Markdown></div>;
});
