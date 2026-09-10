import React from 'react';
import ReactDOM from 'react-dom/client';
import { ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import Entrance from './Entrance';
import './styles.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider
      locale={zhCN}
      theme={{
        token: {
          colorPrimary: '#1e5eff',
          borderRadius: 8,
          fontSize: 14,
        },
      }}
    >
      <Entrance />
    </ConfigProvider>
  </React.StrictMode>,
);
