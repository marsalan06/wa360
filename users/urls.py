from django.urls import path
from . import views

app_name = 'users'

urlpatterns = [
    # API endpoints
    path('api/register/', views.UserRegistrationAPIView.as_view(), name='api_register'),
    path('api/login/', views.login_api, name='api_login'),
    path('api/logout/', views.logout_api, name='api_logout'),
    path('api/profile/', views.profile_api, name='api_profile'),
    
    # Web interface
    path('register/', views.UserRegistrationView.as_view(), name='register'),
    path('login/', views.UserLoginView.as_view(), name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('profile/', views.UserProfileView.as_view(), name='profile'),
    path('', views.UserLoginView.as_view(), name='home'),
]
