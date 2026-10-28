package router

import (
	"github.com/gin-gonic/gin"

	"talkingavatar/backend/internal/app"
	"talkingavatar/backend/internal/config"
	"talkingavatar/backend/internal/handlers/admin"
	"talkingavatar/backend/internal/handlers/live"
)

// RegisterAdmin wires the management-console API (api-admin service).
// Everything here requires an admin session except the auth endpoints and a
// few shared read endpoints the console reuses (live list / status).
func RegisterAdmin(r *gin.Engine, cfg config.Config, deps *app.Deps) {
	avatarHandler := admin.NewAvatarHandler(deps.DB, deps.S3, deps.Queue, cfg.AvatarInitQueueKey, cfg.VideoGenServiceURL)
	knowledgeHandler := admin.NewKnowledgeHandler(deps.DB, deps.S3, cfg.EmbedServerURL)
	taskHandler := admin.NewTaskHandler(deps.DB, deps.Queue, deps.S3)
	chatHandler := live.NewChatHandler(deps.DB)
	liveHandler := live.NewLiveHandler(
		deps.DB, deps.Queue, deps.S3,
		cfg.LiveControlQueueKey, cfg.TaskQueueKey,
		cfg.OpenAIAPIKey, cfg.OpenAIBaseURL, cfg.OpenAIModel,
		cfg.EmbedServerURL, cfg.TTSServiceURL, cfg.OpenAIAPI, cfg.LiveRenderEngine,
	)
	adminHandler := admin.NewAdminHandler(deps.DB, deps.Redis, cfg.AdminUsername, cfg.AdminPassword)

	api := r.Group("/api")
	{
		// ---- Admin auth (login/me/logout are public by design) ----
		api.POST("/admin/login", adminHandler.Login)
		api.GET("/admin/me", adminHandler.Me)
		api.POST("/admin/logout", adminHandler.Logout)

		// ---- Shared read endpoints the admin console uses ----
		api.GET("/live", liveHandler.ListSessions)
		api.GET("/live/:avatarID/status", liveHandler.Status)
		api.GET("/avatars/:id", avatarHandler.Get)
		api.GET("/avatar-defaults", admin.AvatarDefaults(deps.Redis))

		// ---- Protected: admin-only operations ----
		protected := api.Group("", adminHandler.RequireAdmin())
		{
			protected.POST("/tts/preview", admin.PreviewTTS(cfg.TTSServiceURL))
			protected.POST("/avatars", avatarHandler.Create)
			protected.GET("/avatars", avatarHandler.List)
			protected.PUT("/avatars/:id", avatarHandler.Update)
			protected.DELETE("/avatars/:id", avatarHandler.Delete)
			protected.POST("/avatars/:id/retry", avatarHandler.Retry)
			protected.POST("/avatars/:id/skip", avatarHandler.Skip)
			protected.PUT("/avatars/:id/live-settings", avatarHandler.UpdateLiveSettings)
			protected.GET("/avatars/:id/scenes", avatarHandler.ListScenes)
			protected.POST("/avatars/:id/scenes", avatarHandler.CreateScene)
			protected.PUT("/scenes/:id", avatarHandler.UpdateScene)
			protected.DELETE("/scenes/:id", avatarHandler.DeleteScene)
			protected.POST("/scenes/:id/videos", avatarHandler.UploadSceneVideo)
			protected.POST("/scenes/:id/videos/generate", avatarHandler.GenerateSceneVideo)
			protected.PUT("/scenes/:id/videos/:vid", avatarHandler.UpdateSceneVideo)
			protected.DELETE("/scenes/:id/videos/:vid", avatarHandler.DeleteSceneVideo)
			// Knowledge base: avatar -> collection (知识库) -> documents (文档)
			protected.POST("/knowledge-collections", knowledgeHandler.CreateCollection)
			protected.GET("/knowledge-collections", knowledgeHandler.ListCollections)
			protected.GET("/avatars/:id/knowledge-selection", knowledgeHandler.GetKnowledgeSelection)
			protected.POST("/avatars/:id/knowledge-selection", knowledgeHandler.SetKnowledgeSelection)
			protected.PUT("/knowledge-collections/:id", knowledgeHandler.RenameCollection)
			protected.DELETE("/knowledge-collections/:id", knowledgeHandler.DeleteCollection)
			protected.GET("/knowledge-collections/:id/documents", knowledgeHandler.ListDocuments)
			protected.POST("/knowledge-collections/:id/documents", knowledgeHandler.CreateDocument)
			protected.DELETE("/knowledge-collections/:id/documents/:did", knowledgeHandler.DeleteDocument)
			protected.POST("/knowledge-collections/:id/documents/:did/chunks", knowledgeHandler.ListDocumentChunks)
			protected.POST("/knowledge/search", knowledgeHandler.SearchTest)
			protected.POST("/tasks", taskHandler.Create)
			protected.GET("/tasks", taskHandler.List)
			protected.GET("/tasks/:id", taskHandler.Get)
			protected.DELETE("/tasks/:id", taskHandler.Delete)
			protected.POST("/tasks/:id/retry", taskHandler.Retry)
			protected.POST("/live/:avatarID/start", liveHandler.Start)
			protected.POST("/live/:avatarID/stop", liveHandler.Stop)
			protected.POST("/live/:avatarID/push", liveHandler.Push)
			protected.GET("/live/:avatarID/relay", liveHandler.Relay)
			protected.PUT("/live/:avatarID/relay", liveHandler.Relay)
			protected.POST("/projects/read", liveHandler.ReadProject)
			protected.POST("/live/:avatarID/host/plan", liveHandler.HostPlan)
			protected.GET("/live/:avatarID/host", liveHandler.Host)
			protected.PUT("/live/:avatarID/host", liveHandler.Host)
			protected.POST("/live/:avatarID/narrate", liveHandler.NarrateProject)
			protected.GET("/users", chatHandler.ListUsers)
			protected.GET("/chat/logs", chatHandler.Logs)
			protected.POST("/admin/change-name", adminHandler.ChangeName)
			protected.POST("/admin/change-password", adminHandler.ChangePassword)
		}
	}
}
