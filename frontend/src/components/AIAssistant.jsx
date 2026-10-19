import React, { useState, useRef, useEffect } from 'react';
import { Bot, X, Send, User, Maximize2, Minimize2 } from 'lucide-react';
import { t } from '../i18n.js';

export function AIAssistant({ request }) {
  const [isOpen, setIsOpen] = useState(false);
  const [isExpanded, setIsExpanded] = useState(false);
  const [messages, setMessages] = useState([
    { role: 'assistant', text: t('Hi there! I am your AI Assistant. How can I help you today?') }
  ]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const messagesEndRef = useRef(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    if (isOpen) {
      scrollToBottom();
    }
  }, [messages, isOpen]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!input.trim() || loading) return;
    
    const userMessage = input.trim();
    setMessages(prev => [...prev, { role: 'user', text: userMessage }]);
    setInput('');
    setLoading(true);

    try {
      const data = await request('/ai/chat', {
        method: 'POST',
        body: JSON.stringify({ message: userMessage })
      });
      if (data?.reply) {
        setMessages(prev => [...prev, { role: 'assistant', text: data.reply }]);
      } else {
        setMessages(prev => [...prev, { role: 'assistant', text: t('Sorry, I encountered an error connecting to the AI server.') }]);
      }
    } catch (err) {
      setMessages(prev => [...prev, { role: 'assistant', text: t('Sorry, I encountered an error connecting to the AI server.') }]);
    }
    setLoading(false);
  };

  return (
    <>
      {/* Floating Button */}
      {!isOpen && (
        <button 
          className="button button-primary" 
          onClick={() => setIsOpen(true)}
          style={{
            position: 'fixed',
            bottom: '24px',
            right: '24px',
            width: '60px',
            height: '60px',
            borderRadius: '50%',
            padding: 0,
            justifyContent: 'center',
            boxShadow: '0 10px 25px -5px rgba(var(--primary-color-rgb), 0.5)',
            zIndex: 9999,
            animation: 'bounce-in 0.5s cubic-bezier(0.175, 0.885, 0.32, 1.275)'
          }}
        >
          <Bot size={28} />
        </button>
      )}

      {/* Chat Window */}
      {isOpen && (
        <div style={{
          position: 'fixed',
          bottom: isExpanded ? '0' : '24px',
          right: isExpanded ? '0' : '24px',
          width: isExpanded ? '100vw' : '380px',
          height: isExpanded ? '100vh' : '550px',
          background: 'var(--surface)',
          borderRadius: isExpanded ? '0' : '20px',
          boxShadow: '0 20px 40px -10px rgba(0,0,0,0.2)',
          display: 'flex',
          flexDirection: 'column',
          zIndex: 9999,
          border: '1px solid var(--border)',
          overflow: 'hidden',
          transition: 'all 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275)',
          animation: 'slide-up 0.3s ease-out'
        }}>
          {/* Header */}
          <div style={{ 
            background: 'linear-gradient(135deg, var(--primary), #818cf8)', 
            padding: '1rem', 
            display: 'flex', 
            justifyContent: 'space-between', 
            alignItems: 'center',
            color: 'white'
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
              <div style={{ background: 'rgba(255,255,255,0.2)', padding: '8px', borderRadius: '50%' }}>
                <Bot size={20} />
              </div>
              <h3 style={{ margin: 0, fontSize: '1.1rem' }}>AI Assistant</h3>
            </div>
            <div style={{ display: 'flex', gap: '0.5rem' }}>
              <button className="icon-button" style={{ color: 'white' }} onClick={() => setIsExpanded(!isExpanded)}>
                {isExpanded ? <Minimize2 size={18} /> : <Maximize2 size={18} />}
              </button>
              <button className="icon-button" style={{ color: 'white' }} onClick={() => setIsOpen(false)}>
                <X size={18} />
              </button>
            </div>
          </div>

          {/* Messages */}
          <div style={{ flexGrow: 1, padding: '1rem', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '1rem', background: 'var(--bg)' }}>
            {messages.map((msg, idx) => (
              <div key={idx} style={{
                display: 'flex',
                alignItems: 'flex-start',
                gap: '0.75rem',
                flexDirection: msg.role === 'user' ? 'row-reverse' : 'row'
              }}>
                <div style={{ 
                  background: msg.role === 'user' ? 'rgba(var(--primary-color-rgb), 0.1)' : 'var(--surface)', 
                  color: msg.role === 'user' ? 'var(--primary)' : 'var(--text)',
                  padding: '8px', 
                  borderRadius: '50%',
                  border: '1px solid var(--border)',
                  flexShrink: 0
                }}>
                  {msg.role === 'user' ? <User size={16} /> : <Bot size={16} />}
                </div>
                <div style={{
                  background: msg.role === 'user' ? 'linear-gradient(135deg, var(--primary), #818cf8)' : 'var(--surface)',
                  color: msg.role === 'user' ? 'white' : 'var(--text)',
                  padding: '0.75rem 1rem',
                  borderRadius: '16px',
                  borderTopRightRadius: msg.role === 'user' ? '4px' : '16px',
                  borderTopLeftRadius: msg.role === 'assistant' ? '4px' : '16px',
                  boxShadow: '0 2px 5px rgba(0,0,0,0.05)',
                  maxWidth: '80%',
                  lineHeight: '1.5'
                }}>
                  {msg.text}
                </div>
              </div>
            ))}
            {loading && (
              <div style={{ display: 'flex', gap: '0.5rem', padding: '1rem', color: 'var(--text-muted)' }}>
                <Bot size={16} />
                <span className="typing-indicator">AI is typing...</span>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>

          {/* Input */}
          <form onSubmit={handleSubmit} style={{ 
            padding: '1rem', 
            background: 'var(--surface)', 
            borderTop: '1px solid var(--border)',
            display: 'flex',
            gap: '0.5rem'
          }}>
            <input 
              type="text" 
              value={input}
              onChange={e => setInput(e.target.value)}
              placeholder={t('Ask me anything...')}
              style={{ flexGrow: 1, borderRadius: '20px', padding: '0.75rem 1.25rem', border: '1px solid var(--border)' }}
              disabled={loading}
            />
            <button 
              type="submit" 
              className="button button-primary" 
              style={{ borderRadius: '50%', width: '45px', height: '45px', padding: 0, justifyContent: 'center' }}
              disabled={loading || !input.trim()}
            >
              <Send size={18} style={{ marginLeft: '2px' }} />
            </button>
          </form>
        </div>
      )}
      
      <style>{`
        @keyframes bounce-in {
          0% { transform: scale(0); }
          50% { transform: scale(1.1); }
          100% { transform: scale(1); }
        }
        @keyframes slide-up {
          from { opacity: 0; transform: translateY(20px); }
          to { opacity: 1; transform: translateY(0); }
        }
        .typing-indicator {
          animation: blink 1.5s infinite;
        }
        @keyframes blink {
          0%, 100% { opacity: 0.4; }
          50% { opacity: 1; }
        }
      `}</style>
    </>
  );
}
