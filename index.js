import express from 'express';
import session from 'express-session';
import { google } from 'googleapis';
import dotenv from 'dotenv';
import axios from 'axios';

dotenv.config();

const app = express();
const port = 3000;

app.use(express.json());
app.use(session({ secret: 'your_secret_key', resave: false, saveUninitialized: true }));

const oauth2Client = new google.auth.OAuth2(
    process.env.CLIENT_ID,
    process.env.CLIENT_SECRET,
    'http://localhost:3000/auth/callback'
);

const SCOPES = [
    'https://www.googleapis.com/auth/drive.file',
    'https://www.googleapis.com/auth/forms.body'
];

app.get('/auth', (req, res) => {
    const authUrl = oauth2Client.generateAuthUrl({
        access_type: 'offline',
        scope: SCOPES
    });
    res.redirect(authUrl);
});

app.get('/auth/callback', async (req, res) => {
    const { code } = req.query;
    const { tokens } = await oauth2Client.getToken(code);
    req.session.tokens = tokens;
    oauth2Client.setCredentials(tokens);
    res.send('Авторизация успешна! Теперь можно создавать тесты.');
});

app.post('/create-form', async (req, res) => {
    if (!req.session.tokens) return res.status(401).send('Авторизуйтесь через /auth');
    oauth2Client.setCredentials(req.session.tokens);
    
    const formData = {
        info: { title: 'Тест из Google Таблицы', documentTitle: 'Тест' },
        items: [
            { title: 'Вопрос 1', questionItem: { question: { required: true } } },
            { title: 'Вопрос 2', questionItem: { question: { required: true } } }
        ]
    };
    
    try {
        const response = await axios.post('https://forms.googleapis.com/v1/forms', formData, {
            headers: { Authorization: `Bearer ${req.session.tokens.access_token}` }
        });
        res.json(response.data);
    } catch (error) {
        res.status(500).send(error.response.data);
    }
});

app.listen(port, () => console.log(`Сервер запущен на http://localhost:${port}`));
